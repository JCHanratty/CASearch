"""Pytest fixtures for Contract Dashboard tests."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings(temp_dir):
    """Override settings for tests."""
    from app.settings import Settings

    agreements_dir = temp_dir / "agreements"
    agreements_dir.mkdir()

    return Settings(
        DATABASE_PATH=temp_dir / "test.db",
        AGREEMENTS_DIR=agreements_dir,
        ANTHROPIC_API_KEY="test-key",
        MAX_RETRIEVAL_RESULTS=5,
    )


@pytest.fixture
def test_db(test_settings, monkeypatch):
    """Initialize test database."""
    monkeypatch.setattr("app.settings.settings", test_settings)
    monkeypatch.setattr("app.db.settings", test_settings)
    monkeypatch.setattr("app.services.file_scanner.settings", test_settings)
    monkeypatch.setattr("app.services.search.settings", test_settings)

    from app.db import init_db

    init_db()
    yield test_settings


@pytest.fixture
def sample_pdf(test_settings):
    """Create a simple test PDF."""
    # Create a minimal PDF using pypdf
    from pypdf import PdfWriter

    pdf_path = test_settings.AGREEMENTS_DIR / "test_agreement.pdf"
    writer = PdfWriter()

    # Add a page with text
    from pypdf._page import PageObject
    from pypdf.generic import NameObject, DictionaryObject, ArrayObject, NumberObject

    page = PageObject.create_blank_page(width=612, height=792)
    writer.add_page(page)

    with open(pdf_path, "wb") as f:
        writer.write(f)

    return pdf_path


@pytest.fixture
def sample_pdf_with_text(test_settings):
    """Create a test PDF with actual text content using reportlab if available."""
    pdf_path = test_settings.AGREEMENTS_DIR / "contract_sample.pdf"

    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter

        c = canvas.Canvas(str(pdf_path), pagesize=letter)

        # Page 1
        c.drawString(100, 750, "COLLECTIVE AGREEMENT")
        c.drawString(100, 700, "Article 1: Wages")
        c.drawString(100, 680, "The hourly rate shall be $25.00 for all regular employees.")
        c.drawString(100, 660, "Overtime shall be paid at 1.5 times the regular rate.")
        c.drawString(100, 620, "Article 2: Vacation")
        c.drawString(100, 600, "Employees are entitled to 15 days of paid vacation per year.")
        c.showPage()

        # Page 2
        c.drawString(100, 750, "Article 3: Grievance Procedure")
        c.drawString(100, 730, "Step 1: Informal discussion with immediate supervisor.")
        c.drawString(100, 710, "Step 2: Written grievance to department head within 5 days.")
        c.drawString(100, 690, "Step 3: Arbitration if unresolved within 30 days.")
        c.showPage()

        c.save()
    except ImportError:
        # Fallback: create minimal PDF
        from pypdf import PdfWriter

        writer = PdfWriter()
        from pypdf._page import PageObject

        page = PageObject.create_blank_page(width=612, height=792)
        writer.add_page(page)

        with open(pdf_path, "wb") as f:
            writer.write(f)

    return pdf_path


@pytest.fixture
def client(test_db):
    """Create test client."""
    from app.main import app

    return TestClient(app)

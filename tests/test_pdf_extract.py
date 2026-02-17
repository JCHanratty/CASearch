"""Tests for PDF extraction service."""

import pytest
from pathlib import Path


# ============================================================================
# Dehyphenation Tests
# ============================================================================

def test_dehyphenate_simple():
    """Test basic dehyphenation of line-break splits."""
    from app.services.pdf_extract import dehyphenate

    text = "bene-\nfits and over-\ntime"
    result = dehyphenate(text)
    assert "benefits" in result
    assert "overtime" in result


def test_dehyphenate_preserves_compound_words():
    """Test that compound words at line breaks are preserved."""
    from app.services.pdf_extract import dehyphenate

    # When second part starts with uppercase, keep hyphen
    text = "pre-\nApproved"
    result = dehyphenate(text)
    assert "pre-Approved" in result


def test_dehyphenate_no_false_positives():
    """Test that regular hyphens are preserved."""
    from app.services.pdf_extract import dehyphenate

    text = "full-time employment"
    result = dehyphenate(text)
    assert "full-time" in result


# ============================================================================
# Text Normalization Tests
# ============================================================================

def test_normalize_text_whitespace():
    """Test whitespace normalization."""
    from app.services.pdf_extract import normalize_text

    text = "  extra   spaces   here  \n\n\n  another line  "
    result = normalize_text(text)
    assert "extra spaces here" in result
    assert "another line" in result
    # No excessive blank lines
    assert "\n\n\n" not in result


def test_normalize_text_crlf():
    """Test CRLF normalization."""
    from app.services.pdf_extract import normalize_text

    text = "line one\r\nline two\rline three"
    result = normalize_text(text)
    assert "line one" in result
    assert "line two" in result
    assert "line three" in result


# ============================================================================
# Header/Footer Detection Tests
# ============================================================================

def test_detect_repeated_lines_basic():
    """Test detection of repeated lines across pages."""
    from app.services.pdf_extract import detect_repeated_lines

    pages = [
        "Header Line\nContent page 1\nFooter Line",
        "Header Line\nContent page 2\nFooter Line",
        "Header Line\nContent page 3\nFooter Line",
        "Header Line\nContent page 4\nFooter Line",
    ]

    repeated = detect_repeated_lines(pages, threshold=0.6)
    assert "Header Line" in repeated
    assert "Footer Line" in repeated


def test_detect_repeated_lines_threshold():
    """Test that threshold is respected."""
    from app.services.pdf_extract import detect_repeated_lines

    pages = [
        "Common Header\nContent 1",
        "Common Header\nContent 2",
        "Common Header\nContent 3",
        "Rare Header\nContent 4",
        "Another Content\nContent 5",
    ]

    # Common Header appears on 3/5 pages (60%), Rare Header on 1/5 (20%)
    # With threshold 0.6, Common Header should be detected, Rare should not
    repeated = detect_repeated_lines(pages, threshold=0.6)
    assert "Common Header" in repeated
    assert "Rare Header" not in repeated


def test_detect_repeated_lines_too_few_pages():
    """Test that detection requires minimum pages."""
    from app.services.pdf_extract import detect_repeated_lines

    pages = [
        "Header\nContent 1",
        "Header\nContent 2",
    ]

    # Only 2 pages - should return empty
    repeated = detect_repeated_lines(pages, threshold=0.6)
    assert len(repeated) == 0


def test_detect_repeated_lines_preserves_articles():
    """Test that Article headers are not removed."""
    from app.services.pdf_extract import detect_repeated_lines

    pages = [
        "Article 1 Wages\nContent 1",
        "Article 1 Wages\nContent 2",
        "Article 1 Wages\nContent 3",
        "Article 1 Wages\nContent 4",
    ]

    repeated = detect_repeated_lines(pages, threshold=0.6)
    # Article headers should not be in repeated set
    assert not any("Article" in line for line in repeated)


def test_remove_repeated_lines():
    """Test removal of repeated lines from text."""
    from app.services.pdf_extract import remove_repeated_lines

    text = "Header Line\nActual Content\nFooter Line"
    repeated = {"Header Line", "Footer Line"}

    result = remove_repeated_lines(text, repeated)
    assert "Header Line" not in result
    assert "Footer Line" not in result
    assert "Actual Content" in result


# ============================================================================
# PageText Structure Tests
# ============================================================================

def test_page_text_has_raw_text():
    """Test that PageText includes both text and raw_text."""
    from app.services.pdf_extract import PageText

    page = PageText(page_number=1, text="cleaned", raw_text="original text")
    assert page.text == "cleaned"
    assert page.raw_text == "original text"


# ============================================================================
# Integration Tests
# ============================================================================

def test_extract_pdf_deterministic(test_db, sample_pdf):
    """Test that PDF extraction is deterministic."""
    from app.services.pdf_extract import extract_pdf_pages

    pages1 = extract_pdf_pages(sample_pdf)
    pages2 = extract_pdf_pages(sample_pdf)

    assert len(pages1) == len(pages2)

    for p1, p2 in zip(pages1, pages2):
        assert p1.page_number == p2.page_number
        assert p1.text == p2.text
        assert p1.raw_text == p2.raw_text


def test_extract_returns_page_numbers(test_db, sample_pdf):
    """Test that extracted pages have correct page numbers."""
    from app.services.pdf_extract import extract_pdf_pages

    pages = extract_pdf_pages(sample_pdf)

    # All pages should have page_number starting at 1
    assert all(p.page_number >= 1 for p in pages)

    # Page numbers should be sequential
    page_nums = [p.page_number for p in pages]
    assert page_nums == list(range(1, len(pages) + 1))


def test_extract_invalid_pdf(test_db, test_settings):
    """Test extraction of invalid PDF raises error."""
    from app.services.pdf_extract import extract_pdf_pages, ExtractionError

    invalid_path = test_settings.AGREEMENTS_DIR / "not_a_pdf.pdf"
    invalid_path.write_text("This is not a PDF")

    with pytest.raises(ExtractionError):
        extract_pdf_pages(invalid_path)


def test_get_pdf_page_count(test_db, sample_pdf):
    """Test getting page count."""
    from app.services.pdf_extract import get_pdf_page_count

    count = get_pdf_page_count(sample_pdf)
    assert count >= 1


def test_extract_with_header_footer_stripping(test_db, sample_pdf):
    """Test extraction with header/footer stripping enabled."""
    from app.services.pdf_extract import extract_pdf_pages

    pages = extract_pdf_pages(sample_pdf, strip_headers_footers=True)
    assert len(pages) >= 1
    # Each page should have both text and raw_text
    for page in pages:
        assert hasattr(page, 'text')
        assert hasattr(page, 'raw_text')

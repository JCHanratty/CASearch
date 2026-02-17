"""Tests for file scanner service."""

import pytest
from pathlib import Path


def test_scan_empty_directory(test_db):
    """Test scanning an empty directory."""
    from app.services.file_scanner import scan_agreements

    results = scan_agreements()

    assert results["new"] == 0
    assert results["changed"] == 0
    assert results["unchanged"] == 0
    assert results["missing"] == 0


def test_scan_finds_new_pdf(test_db, sample_pdf):
    """Test that scanning finds a new PDF file."""
    from app.services.file_scanner import scan_agreements, get_all_files

    results = scan_agreements()

    assert results["new"] == 1
    assert results["unchanged"] == 0

    files = get_all_files()
    assert len(files) == 1
    assert files[0].filename == "test_agreement.pdf"
    assert files[0].status == "pending"


def test_scan_detects_unchanged(test_db, sample_pdf):
    """Test that unchanged files are detected."""
    from app.services.file_scanner import scan_agreements

    # First scan
    scan_agreements()

    # Second scan
    results = scan_agreements()

    assert results["new"] == 0
    assert results["unchanged"] == 1


def test_compute_sha256(test_db, sample_pdf):
    """Test SHA256 computation."""
    from app.services.file_scanner import compute_sha256

    hash1 = compute_sha256(sample_pdf)
    hash2 = compute_sha256(sample_pdf)

    # Same file should produce same hash (deterministic)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 is 64 hex chars


def test_get_file_by_id(test_db, sample_pdf):
    """Test getting file by ID."""
    from app.services.file_scanner import scan_agreements, get_file_by_id, get_all_files

    scan_agreements()
    files = get_all_files()
    assert len(files) == 1

    file = get_file_by_id(files[0].id)
    assert file is not None
    assert file.filename == "test_agreement.pdf"


def test_get_file_by_id_not_found(test_db):
    """Test getting non-existent file."""
    from app.services.file_scanner import get_file_by_id

    file = get_file_by_id(999)
    assert file is None

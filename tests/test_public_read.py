"""Tests for public_read document access control."""

import pytest


def test_file_default_not_public(test_db, sample_pdf):
    """Test that newly scanned files default to public_read=False."""
    from app.services.file_scanner import scan_agreements, get_all_files

    # Scan to pick up the sample PDF
    results = scan_agreements()
    assert results["new"] == 1

    # Get the file and verify public_read is False by default
    files = get_all_files()
    assert len(files) == 1
    assert files[0].public_read is False


def test_toggle_public_read(test_db, sample_pdf):
    """Test toggling public_read status."""
    from app.services.file_scanner import scan_agreements, get_all_files, get_file_by_id
    from app.db import toggle_file_public_read

    # Scan to pick up the sample PDF
    scan_agreements()
    files = get_all_files()
    file_id = files[0].id

    # Initially should be private
    assert files[0].public_read is False

    # Toggle to public
    new_status = toggle_file_public_read(file_id)
    assert new_status is True

    # Verify the change persisted
    file = get_file_by_id(file_id)
    assert file.public_read is True

    # Toggle back to private
    new_status = toggle_file_public_read(file_id)
    assert new_status is False

    # Verify the change persisted
    file = get_file_by_id(file_id)
    assert file.public_read is False


def test_get_public_files_only_returns_public(test_db, test_settings):
    """Test that get_public_files only returns files with public_read=True."""
    from pypdf import PdfWriter
    from pypdf._page import PageObject
    from app.services.file_scanner import scan_agreements, get_all_files, get_public_files
    from app.db import toggle_file_public_read

    # Create multiple test PDFs
    for i in range(3):
        pdf_path = test_settings.AGREEMENTS_DIR / f"test_doc_{i}.pdf"
        writer = PdfWriter()
        page = PageObject.create_blank_page(width=612, height=792)
        writer.add_page(page)
        with open(pdf_path, "wb") as f:
            writer.write(f)

    # Scan to pick up all PDFs
    scan_agreements()
    files = get_all_files()
    assert len(files) == 3

    # Initially no files should be public
    public_files = get_public_files()
    assert len(public_files) == 0

    # Make one file public
    toggle_file_public_read(files[0].id)

    # Should now have 1 public file
    public_files = get_public_files()
    assert len(public_files) == 1
    assert public_files[0].id == files[0].id
    assert public_files[0].public_read is True

    # Make another file public
    toggle_file_public_read(files[1].id)

    # Should now have 2 public files
    public_files = get_public_files()
    assert len(public_files) == 2

    # Toggle first file back to private
    toggle_file_public_read(files[0].id)

    # Should now have 1 public file again
    public_files = get_public_files()
    assert len(public_files) == 1
    assert public_files[0].id == files[1].id


def test_toggle_nonexistent_file(test_db):
    """Test that toggling a non-existent file raises an error."""
    from app.db import toggle_file_public_read

    with pytest.raises(ValueError, match="File with id 99999 not found"):
        toggle_file_public_read(99999)


def test_toggle_public_endpoint(client, test_db, sample_pdf):
    """Test the toggle-public endpoint via HTTP."""
    from app.services.file_scanner import scan_agreements, get_all_files

    # Scan to pick up the sample PDF
    scan_agreements()
    files = get_all_files()
    file_id = files[0].id

    # Initially should be private
    assert files[0].public_read is False

    # Call the toggle endpoint
    response = client.post(
        f"/documents/{file_id}/toggle-public",
        headers={"HX-Request": "true"}
    )
    assert response.status_code == 200

    # Verify the file is now public
    files = get_all_files()
    assert files[0].public_read is True

    # Toggle again
    response = client.post(
        f"/documents/{file_id}/toggle-public",
        headers={"HX-Request": "true"}
    )
    assert response.status_code == 200

    # Verify the file is now private again
    files = get_all_files()
    assert files[0].public_read is False


def test_toggle_public_endpoint_not_found(client, test_db):
    """Test that toggle endpoint returns 404 for non-existent file."""
    response = client.post("/documents/99999/toggle-public")
    assert response.status_code == 404

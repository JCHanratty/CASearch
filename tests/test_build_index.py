"""Tests for the build_index packaging script."""

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

# Add tools directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from build_index import (
    build_index,
    compute_sha256,
    create_index_schema,
    get_version_from_env,
    package_index,
    write_checksum,
)


# --- Version extraction tests ---

def test_get_version_from_env_with_tag(monkeypatch):
    """Test version extraction from GITHUB_REF tag."""
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v1.2.3")
    assert get_version_from_env() == "1.2.3"


def test_get_version_from_env_with_v_prefix(monkeypatch):
    """Test version extraction strips v prefix."""
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v2.0.0")
    assert get_version_from_env() == "2.0.0"


def test_get_version_from_env_no_ref(monkeypatch):
    """Test default version when no GITHUB_REF."""
    monkeypatch.delenv("GITHUB_REF", raising=False)
    assert get_version_from_env() == "0.0.0"


def test_get_version_from_env_branch_ref(monkeypatch):
    """Test default version for branch refs."""
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert get_version_from_env() == "0.0.0"


# --- Checksum tests ---

def test_compute_sha256(tmp_path):
    """Test SHA256 computation."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = compute_sha256(test_file)

    # Verify against known hash
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert result == expected


def test_write_checksum(tmp_path):
    """Test checksum file writing."""
    test_file = tmp_path / "test.zip"
    test_file.write_bytes(b"fake zip content")

    checksum_file = tmp_path / "test.zip.sha256"
    result = write_checksum(test_file, checksum_file)

    assert checksum_file.exists()
    content = checksum_file.read_text()
    assert result in content
    assert "test.zip" in content


# --- Schema tests ---

def test_create_index_schema(tmp_path):
    """Test database schema creation."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    create_index_schema(conn)
    conn.commit()

    # Verify tables exist
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    tables = {row[0] for row in cursor.fetchall()}

    assert "files" in tables
    assert "pdf_pages" in tables
    assert "page_fts" in tables
    assert "metadata" in tables

    conn.close()


# --- Build index tests ---

def test_build_index_dry_run(tmp_path):
    """Test build_index in dry-run mode."""
    # Create test agreements directory with a dummy file
    agreements_dir = tmp_path / "agreements"
    agreements_dir.mkdir()

    # Create a minimal test PDF (or just a file for dry-run)
    test_pdf = agreements_dir / "test_agreement.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 fake pdf content for testing")

    output_dir = tmp_path / "output"

    result = build_index(
        agreements_dir=agreements_dir,
        output_dir=output_dir,
        version="1.0.0",
        dry_run=True,
    )

    # Verify database was created
    assert result["db_path"].exists()
    assert result["stats"]["files_processed"] == 1
    assert result["stats"]["pages_indexed"] >= 1

    # Verify database content
    conn = sqlite3.connect(str(result["db_path"]))
    conn.row_factory = sqlite3.Row

    # Check files table
    files = conn.execute("SELECT * FROM files").fetchall()
    assert len(files) == 1
    assert files[0]["filename"] == "test_agreement.pdf"

    # Check pages table
    pages = conn.execute("SELECT * FROM pdf_pages").fetchall()
    assert len(pages) >= 1

    # Check FTS index
    fts_count = conn.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0]
    assert fts_count >= 1

    conn.close()


def test_build_index_empty_directory(tmp_path):
    """Test build_index with empty agreements directory."""
    agreements_dir = tmp_path / "empty_agreements"
    agreements_dir.mkdir()

    output_dir = tmp_path / "output"

    result = build_index(
        agreements_dir=agreements_dir,
        output_dir=output_dir,
        version="1.0.0",
        dry_run=True,
    )

    assert result["db_path"].exists()
    assert result["stats"]["files_processed"] == 0
    assert result["stats"]["pages_indexed"] == 0


def test_build_index_nested_directories(tmp_path):
    """Test build_index finds PDFs in nested directories."""
    agreements_dir = tmp_path / "agreements"
    nested_dir = agreements_dir / "subdir" / "deep"
    nested_dir.mkdir(parents=True)

    # Create PDFs at different levels
    (agreements_dir / "root.pdf").write_bytes(b"%PDF-1.4 root")
    (nested_dir / "nested.pdf").write_bytes(b"%PDF-1.4 nested")

    output_dir = tmp_path / "output"

    result = build_index(
        agreements_dir=agreements_dir,
        output_dir=output_dir,
        version="1.0.0",
        dry_run=True,
    )

    assert result["stats"]["files_processed"] == 2


# --- Package tests ---

def test_package_index(tmp_path):
    """Test index packaging into zip."""
    # Create a test database
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_index_schema(conn)
    conn.execute("INSERT INTO metadata (key, value) VALUES ('version', '1.0.0')")
    conn.commit()
    conn.close()

    output_dir = tmp_path / "dist"

    result = package_index(
        db_path=db_path,
        output_dir=output_dir,
        version="1.0.0",
    )

    # Verify zip was created
    assert result["zip_path"].exists()
    assert result["zip_path"].name == "index-v1.0.0.zip"

    # Verify checksum file
    assert result["checksum_path"].exists()
    assert result["checksum_path"].name == "index-v1.0.0.zip.sha256"

    # Verify zip contents
    with zipfile.ZipFile(result["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "index.sqlite" in names
        assert "metadata.json" in names

        # Verify metadata
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["version"] == "1.0.0"
        assert metadata["format"] == "sqlite-fts5"


def test_package_index_checksum_valid(tmp_path):
    """Test that checksum file matches zip content."""
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_index_schema(conn)
    conn.commit()
    conn.close()

    output_dir = tmp_path / "dist"

    result = package_index(
        db_path=db_path,
        output_dir=output_dir,
        version="2.0.0",
    )

    # Verify checksum matches
    actual_checksum = compute_sha256(result["zip_path"])
    assert result["checksum"] == actual_checksum

    # Verify checksum file content
    checksum_content = result["checksum_path"].read_text()
    assert actual_checksum in checksum_content


# --- Integration tests ---

def test_full_build_and_package_workflow(tmp_path):
    """Test complete build and package workflow."""
    # Setup
    agreements_dir = tmp_path / "agreements"
    agreements_dir.mkdir()

    # Create test PDFs
    for i in range(3):
        pdf_path = agreements_dir / f"agreement_{i}.pdf"
        pdf_path.write_bytes(f"%PDF-1.4 content {i}".encode())

    output_dir = tmp_path / "dist"

    # Build
    build_result = build_index(
        agreements_dir=agreements_dir,
        output_dir=output_dir,
        version="1.0.0",
        dry_run=True,
    )

    assert build_result["stats"]["files_processed"] == 3

    # Package
    package_result = package_index(
        db_path=build_result["db_path"],
        output_dir=output_dir,
        version="1.0.0",
    )

    # Verify outputs
    assert package_result["zip_path"].exists()
    assert package_result["checksum_path"].exists()

    # Verify zip is valid and extractable
    with zipfile.ZipFile(package_result["zip_path"], "r") as zf:
        # Test extraction
        extract_dir = tmp_path / "extracted"
        zf.extractall(extract_dir)

        # Verify extracted database is usable
        extracted_db = extract_dir / "index.sqlite"
        assert extracted_db.exists()

        conn = sqlite3.connect(str(extracted_db))
        files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert files_count == 3
        conn.close()


def test_build_index_stores_metadata(tmp_path):
    """Test that build stores version metadata in database."""
    agreements_dir = tmp_path / "agreements"
    agreements_dir.mkdir()

    output_dir = tmp_path / "output"

    result = build_index(
        agreements_dir=agreements_dir,
        output_dir=output_dir,
        version="3.2.1",
        dry_run=True,
    )

    conn = sqlite3.connect(str(result["db_path"]))
    version = conn.execute(
        "SELECT value FROM metadata WHERE key = 'version'"
    ).fetchone()[0]

    assert version == "3.2.1"
    conn.close()

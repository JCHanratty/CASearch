#!/usr/bin/env python3
"""
Build and package index artifacts for distribution.

This script:
1. Extracts text from PDFs in the agreements directory
2. Builds an SQLite index with FTS5 search
3. Packages everything into index-v{version}.zip
4. Creates a SHA256 checksum file
5. Optionally publishes to GitHub Releases
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def get_version_from_env() -> str:
    """Extract version from GITHUB_REF env var or return default."""
    github_ref = os.environ.get("GITHUB_REF", "")
    if github_ref.startswith("refs/tags/"):
        return github_ref.replace("refs/tags/", "").lstrip("v")
    return "0.0.0"


def compute_sha256(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def write_checksum(zip_path: Path, checksum_path: Path) -> str:
    """Write SHA256 checksum file for the zip."""
    checksum = compute_sha256(zip_path)
    checksum_path.write_text(f"{checksum}  {zip_path.name}\n")
    return checksum


def create_index_schema(conn: sqlite3.Connection) -> None:
    """Create the index database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            pages INTEGER DEFAULT 0,
            indexed_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pdf_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(file_id, page_number)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
            file_id UNINDEXED,
            page_id UNINDEXED,
            page_number UNINDEXED,
            text,
            tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)


def try_import_extractors():
    """Try to import existing extraction functions."""
    try:
        from app.services.pdf_extract import extract_pdf_pages
        return extract_pdf_pages
    except ImportError:
        return None


def fallback_extract_pdf(filepath: Path) -> list:
    """
    Fallback PDF extraction using pypdf if available.
    Returns list of dicts with page_number and text.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(filepath))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append({"page_number": i, "text": text.strip()})
        return pages
    except ImportError:
        # No pypdf available, return minimal placeholder
        return [{"page_number": 1, "text": f"Content from {filepath.name}"}]
    except Exception as e:
        print(f"Warning: Could not extract {filepath}: {e}")
        return [{"page_number": 1, "text": f"Error extracting {filepath.name}"}]


def build_index(
    agreements_dir: Path,
    output_dir: Path,
    version: str,
    dry_run: bool = False,
) -> dict:
    """
    Build index from PDF files.

    Args:
        agreements_dir: Directory containing PDF files
        output_dir: Directory to write output files
        version: Version string for the index
        dry_run: If True, don't actually process PDFs (for testing)

    Returns:
        dict with paths to created files and stats
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create index database
    db_path = output_dir / "index.sqlite"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_index_schema(conn)

    # Store metadata
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        ("version", version)
    )
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        ("created_at", "datetime('now')")
    )

    # Try to use existing extractors
    extract_func = try_import_extractors()

    # Find and process PDFs
    pdf_files = list(agreements_dir.glob("**/*.pdf"))
    stats = {"files_processed": 0, "pages_indexed": 0, "errors": []}

    for pdf_path in pdf_files:
        try:
            # Compute file hash
            file_hash = compute_sha256(pdf_path)

            # Extract pages
            if dry_run:
                pages = [{"page_number": 1, "text": f"Dry run: {pdf_path.name}"}]
            elif extract_func:
                # Use existing extractor
                page_objects = extract_func(pdf_path)
                pages = [{"page_number": p.page_number, "text": p.text} for p in page_objects]
            else:
                # Use fallback
                pages = fallback_extract_pdf(pdf_path)

            # Insert file record
            cursor = conn.execute(
                """INSERT INTO files (path, filename, sha256, pages)
                   VALUES (?, ?, ?, ?)""",
                (str(pdf_path.relative_to(agreements_dir)), pdf_path.name, file_hash, len(pages))
            )
            file_id = cursor.lastrowid

            # Insert pages and FTS entries
            for page in pages:
                page_cursor = conn.execute(
                    """INSERT INTO pdf_pages (file_id, page_number, text)
                       VALUES (?, ?, ?)""",
                    (file_id, page["page_number"], page["text"])
                )
                page_id = page_cursor.lastrowid

                conn.execute(
                    """INSERT INTO page_fts (file_id, page_id, page_number, text)
                       VALUES (?, ?, ?, ?)""",
                    (file_id, page_id, page["page_number"], page["text"])
                )
                stats["pages_indexed"] += 1

            stats["files_processed"] += 1
            print(f"  Indexed: {pdf_path.name} ({len(pages)} pages)")

        except Exception as e:
            stats["errors"].append({"file": str(pdf_path), "error": str(e)})
            print(f"  Error: {pdf_path.name}: {e}")

    conn.commit()
    conn.close()

    return {
        "db_path": db_path,
        "stats": stats,
    }


def package_index(
    db_path: Path,
    output_dir: Path,
    version: str,
) -> dict:
    """
    Package index database into a versioned zip file.

    Args:
        db_path: Path to the index.sqlite file
        output_dir: Directory to write zip file
        version: Version string

    Returns:
        dict with zip_path and checksum_path
    """
    zip_name = f"index-v{version}.zip"
    zip_path = output_dir / zip_name
    checksum_path = output_dir / f"{zip_name}.sha256"

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create zip file
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add database
        zf.write(db_path, "index.sqlite")

        # Add metadata JSON
        metadata = {
            "version": version,
            "format": "sqlite-fts5",
            "files": ["index.sqlite"],
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

    # Write checksum
    checksum = write_checksum(zip_path, checksum_path)

    return {
        "zip_path": zip_path,
        "checksum_path": checksum_path,
        "checksum": checksum,
    }


def publish_release(
    zip_path: Path,
    checksum_path: Path,
    version: str,
    repo: str,
    token: str,
) -> dict:
    """
    Publish zip to GitHub Releases.

    Args:
        zip_path: Path to the zip file
        checksum_path: Path to the checksum file
        version: Version tag
        repo: GitHub repo in format "owner/repo"
        token: GitHub API token

    Returns:
        dict with release info
    """
    tag = f"v{version}"
    api_base = f"https://api.github.com/repos/{repo}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if release exists
    try:
        req = Request(f"{api_base}/releases/tags/{tag}", headers=headers)
        with urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read().decode())
            release_id = release["id"]
            print(f"  Found existing release: {tag}")
    except HTTPError as e:
        if e.code == 404:
            # Create new release
            print(f"  Creating release: {tag}")
            release_data = json.dumps({
                "tag_name": tag,
                "name": f"Index {tag}",
                "body": f"Pre-built index package version {version}",
                "draft": False,
                "prerelease": False,
            }).encode()

            req = Request(
                f"{api_base}/releases",
                data=release_data,
                headers={**headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                release = json.loads(resp.read().decode())
                release_id = release["id"]
        else:
            raise

    # Upload assets
    upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
    uploaded = []

    for asset_path in [zip_path, checksum_path]:
        asset_name = asset_path.name
        content_type = "application/zip" if asset_name.endswith(".zip") else "text/plain"

        print(f"  Uploading: {asset_name}")

        with open(asset_path, "rb") as f:
            data = f.read()

        req = Request(
            f"{upload_url}?name={asset_name}",
            data=data,
            headers={
                **headers,
                "Content-Type": content_type,
                "Content-Length": str(len(data)),
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=300) as resp:
                asset_info = json.loads(resp.read().decode())
                uploaded.append(asset_info["browser_download_url"])
        except HTTPError as e:
            if e.code == 422:
                # Asset already exists
                print(f"    Asset already exists: {asset_name}")
            else:
                raise

    return {
        "release_id": release_id,
        "tag": tag,
        "assets": uploaded,
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build and package index artifacts for distribution."
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Version string (default: from GITHUB_REF or 0.0.0)",
    )
    parser.add_argument(
        "--agreements-dir",
        type=Path,
        default=Path("data/agreements"),
        help="Directory containing PDF files (default: data/agreements)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Output directory for artifacts (default: dist)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish to GitHub Releases (requires GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repo for publishing (default: from GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually process PDFs (for testing)",
    )

    args = parser.parse_args()

    # Determine version
    version = args.version or get_version_from_env()
    print(f"Building index version: {version}")

    # Validate agreements directory
    if not args.agreements_dir.exists():
        print(f"Error: Agreements directory not found: {args.agreements_dir}")
        sys.exit(1)

    # Build index
    print(f"Processing PDFs from: {args.agreements_dir}")
    build_result = build_index(
        agreements_dir=args.agreements_dir,
        output_dir=args.output_dir,
        version=version,
        dry_run=args.dry_run,
    )

    stats = build_result["stats"]
    print(f"\nBuild complete:")
    print(f"  Files processed: {stats['files_processed']}")
    print(f"  Pages indexed: {stats['pages_indexed']}")
    if stats["errors"]:
        print(f"  Errors: {len(stats['errors'])}")

    # Package
    print(f"\nPackaging index...")
    package_result = package_index(
        db_path=build_result["db_path"],
        output_dir=args.output_dir,
        version=version,
    )

    print(f"  Created: {package_result['zip_path']}")
    print(f"  Checksum: {package_result['checksum']}")

    # Publish if requested
    if args.publish:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("\nError: --publish requires GITHUB_TOKEN environment variable")
            sys.exit(1)

        repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            print("\nError: --publish requires --repo or GITHUB_REPOSITORY env var")
            sys.exit(1)

        print(f"\nPublishing to GitHub Releases ({repo})...")
        try:
            publish_result = publish_release(
                zip_path=package_result["zip_path"],
                checksum_path=package_result["checksum_path"],
                version=version,
                repo=repo,
                token=token,
            )
            print(f"  Release: {publish_result['tag']}")
            for url in publish_result["assets"]:
                print(f"  Asset: {url}")
        except (HTTPError, URLError) as e:
            print(f"\nError publishing: {e}")
            sys.exit(1)

    print(f"\nDone! Output: {package_result['zip_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

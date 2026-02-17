#!/usr/bin/env python3
"""
Build or rebuild the TF-IDF vector index for RAG search.

This script indexes all pages from indexed PDF files into a TF-IDF vector store
that enables semantic similarity search.

Usage:
    python tools/build_vector_index.py [--force]

Options:
    --force     Rebuild index even if it already exists
"""

import sys
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import init_db, get_db
from app.services.rag import rebuild_vector_index, get_vector_index_stats


def progress_callback(current: int, total: int, message: str) -> None:
    """Print progress updates."""
    if total > 0:
        pct = (current / total) * 100
        bar_len = 40
        filled = int(bar_len * current / total)
        bar = "=" * filled + "-" * (bar_len - filled)
        print(f"\r[{bar}] {pct:5.1f}% | {message}", end="", flush=True)
    else:
        print(f"\r{message}", end="", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Build or rebuild the TF-IDF vector index for RAG search."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild index even if it already exists"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("TF-IDF Vector Index Builder")
    print("=" * 60)
    print()

    # Initialize database (ensures tables exist)
    print("Initializing database...")
    init_db()

    # Check current index status
    stats = get_vector_index_stats()
    print(f"Current index status:")
    print(f"  - Index exists: {stats['index_exists']}")
    print(f"  - Index loaded: {stats['index_loaded']}")
    print(f"  - Pages indexed: {stats['pages_indexed']}")
    print(f"  - Vocabulary size: {stats['vocabulary_size']}")
    if stats.get('index_size_mb'):
        print(f"  - Index size: {stats['index_size_mb']} MB")
    print()

    # Check if we have pages to index
    with get_db() as conn:
        total_pages = conn.execute("""
            SELECT COUNT(*) FROM pdf_pages p
            JOIN files f ON p.file_id = f.id
            WHERE f.status = 'indexed' AND p.text IS NOT NULL AND length(p.text) > 0
        """).fetchone()[0]

        total_files = conn.execute("""
            SELECT COUNT(DISTINCT f.id) FROM files f
            JOIN pdf_pages p ON p.file_id = f.id
            WHERE f.status = 'indexed'
        """).fetchone()[0]

    print(f"Found {total_pages} pages across {total_files} indexed files.")
    print()

    if total_pages == 0:
        print("No pages to index. Please index some PDF files first.")
        print("Use the web dashboard or run the indexer service.")
        return 1

    if stats['index_exists'] and not args.force:
        print("Index already exists. Use --force to rebuild.")
        print()

        # Check if index is out of date
        if stats['pages_indexed'] != total_pages:
            print(f"Note: Index has {stats['pages_indexed']} pages but database has {total_pages} pages.")
            print("Consider running with --force to rebuild.")
        return 0

    # Build the index
    print("Building vector index...")
    print("-" * 60)

    result = rebuild_vector_index(progress_callback=progress_callback)
    print()  # New line after progress bar
    print("-" * 60)
    print()

    if result['success']:
        print("Index built successfully!")
        print(f"  - Pages indexed: {result['pages_indexed']}")
        print(f"  - Vocabulary size: {result.get('vocabulary_size', 'N/A')}")
        print()

        # Show updated stats
        new_stats = get_vector_index_stats()
        if new_stats.get('index_size_mb'):
            print(f"  - Index size: {new_stats['index_size_mb']} MB")
        return 0
    else:
        print(f"Error building index: {result['message']}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

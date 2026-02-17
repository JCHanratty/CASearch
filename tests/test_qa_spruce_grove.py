from app.db import get_db
from app.services.search import search_pages


def test_search_finds_spruce_grove(test_db):
    # Insert a file and a page containing the target phrase, and add to FTS
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO files (path, filename, sha256, mtime, size, status) VALUES (?, ?, ?, ?, ?, 'indexed')",
            ("data/agreements/test_spruce.pdf", "test_spruce.pdf", "sha", 0, 0),
        )
        file_id = cur.lastrowid

        conn.execute(
            "INSERT INTO pdf_pages (file_id, page_number, text, raw_text) VALUES (?, ?, ?, ?)",
            (
                file_id,
                1,
                "Spruce Grove Sick Time: Employees are entitled to 5 days sick leave per year.",
                "Spruce Grove Sick Time: Employees are entitled to 5 days sick leave per year.",
            ),
        )

        page_row = conn.execute("SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)).fetchone()
        conn.execute(
            "INSERT INTO page_fts (file_id, page_id, page_number, text) VALUES (?, ?, ?, ?)",
            (file_id, page_row["id"], 1, "Spruce Grove Sick Time: Employees are entitled to 5 days sick leave per year."),
        )

    # Now perform a search that previously returned no results
    results = search_pages("Spruce Grove Sick Time", limit=5)
    assert len(results) > 0
    # Ensure snippet contains 'Sick'
    assert any("sick" in (r.snippet or "").lower() for r in results)

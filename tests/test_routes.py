"""Tests for API routes."""

import pytest


def test_dashboard_loads(client):
    """Test dashboard page loads."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Contract Dashboard" in response.text


def test_documents_page_loads(client):
    """Test documents page loads."""
    response = client.get("/documents")
    assert response.status_code == 200
    assert "Documents" in response.text


def test_search_page_loads(client):
    """Test search page loads."""
    response = client.get("/search")
    assert response.status_code == 200
    assert "Search" in response.text


def test_search_with_query(client):
    """Test search with query parameter."""
    response = client.get("/search?q=wages")
    assert response.status_code == 200


def test_qa_page_loads(client):
    """Test Q&A page loads."""
    response = client.get("/qa")
    assert response.status_code == 200
    assert "Q&A" in response.text


def test_compare_page_loads(client):
    """Test compare page loads."""
    response = client.get("/compare")
    assert response.status_code == 200
    assert "Compare" in response.text


def test_diagnostics_page_loads(client):
    """Test diagnostics page loads."""
    response = client.get("/admin/config")
    assert response.status_code == 200
    assert "Diagnostics" in response.text


def test_documents_scan(client, sample_pdf):
    """Test document scanning endpoint."""
    response = client.post("/documents/scan")
    assert response.status_code == 200


def test_search_htmx_partial(client):
    """Test search HTMX partial response."""
    response = client.get(
        "/search?q=wages",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Should return partial HTML, not full page
    assert "<!DOCTYPE html>" not in response.text


def test_document_not_found(client):
    """Test 404 for non-existent document."""
    response = client.get("/documents/999/view")
    assert response.status_code == 404


# --- JSON API Endpoint Tests ---

def test_admin_health_json(client):
    """Test GET /admin/health returns JSON with status and db info."""
    response = client.get("/admin/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert "db" in data
    assert isinstance(data["db"], dict)
    assert "total_files" in data["db"]
    assert "indexed_files" in data["db"]
    assert "version" in data
    from app.version import __version__
    assert data["version"] == __version__


def test_admin_fts_status_json(client):
    """Test GET /admin/fts-status returns FTS sync status as JSON."""
    response = client.get("/admin/fts-status")
    assert response.status_code == 200

    data = response.json()
    assert "in_sync" in data
    assert isinstance(data["in_sync"], bool)
    assert "out_of_sync" in data
    assert isinstance(data["out_of_sync"], list)


def test_admin_rebuild_fts_json(client):
    """Test POST /admin/rebuild-fts-json rebuilds FTS and returns JSON."""
    response = client.post("/admin/rebuild-fts-json")
    assert response.status_code == 200

    data = response.json()
    assert "rebuilt" in data
    assert data["rebuilt"] is True
    assert "pages_indexed" in data
    assert isinstance(data["pages_indexed"], int)

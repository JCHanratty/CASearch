"""Tests for the bug report feature."""

import pytest

from app.db import get_db


def test_get_bug_report_form_html(client):
    """Test GET /admin/bug-report/form returns the form HTML."""
    response = client.get("/admin/bug-report/form")

    assert response.status_code == 200
    assert "<form" in response.text
    assert "reporter_name" in response.text
    assert "reporter_email" in response.text
    assert "subject" in response.text
    assert "description" in response.text
    assert "severity" in response.text


def test_submit_bug_report_htmx_creates_record(client, test_db):
    """Test POST form submission via HTMX creates a database record."""
    form_data = {
        "reporter_name": "Test User",
        "reporter_email": "test@example.com",
        "subject": "Test Bug Report",
        "description": "This is a test description that is long enough.",
        "severity": "medium",
    }

    response = client.post(
        "/admin/bug-report",
        data=form_data,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Report Submitted" in response.text or "success" in response.text.lower()

    # Verify record was created in database
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bug_reports WHERE subject = ?",
            ("Test Bug Report",)
        ).fetchone()

        assert row is not None
        assert row["reporter_name"] == "Test User"
        assert row["reporter_email"] == "test@example.com"
        assert row["description"] == "This is a test description that is long enough."
        assert row["severity"] == "medium"
        assert row["status"] == "open"


def test_submit_bug_report_json_api(client, test_db):
    """Test POST JSON to /api/bug-reports creates a record."""
    json_data = {
        "reporter_name": "API User",
        "reporter_email": "api@example.com",
        "subject": "API Test Bug",
        "description": "This is an API test description.",
        "severity": "high",
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["status"] == "created"

    # Verify record was created in database
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bug_reports WHERE id = ?",
            (data["id"],)
        ).fetchone()

        assert row is not None
        assert row["subject"] == "API Test Bug"
        assert row["severity"] == "high"


def test_submit_bug_report_validation_failure_short_subject(client, test_db):
    """Test POST JSON with short subject returns 400."""
    json_data = {
        "subject": "Sho",  # Too short (< 5 chars)
        "description": "This is a valid description.",
        "severity": "low",
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 400
    data = response.json()
    assert "errors" in data
    assert "subject" in data["errors"]


def test_submit_bug_report_validation_failure_short_description(client, test_db):
    """Test POST JSON with short description returns 400."""
    json_data = {
        "subject": "Valid Subject",
        "description": "Short",  # Too short (< 10 chars)
        "severity": "low",
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 400
    data = response.json()
    assert "errors" in data
    assert "description" in data["errors"]


def test_submit_bug_report_validation_failure_invalid_severity(client, test_db):
    """Test POST JSON with invalid severity returns 400."""
    json_data = {
        "subject": "Valid Subject",
        "description": "This is a valid description.",
        "severity": "invalid",  # Not in allowed values
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 400
    data = response.json()
    assert "errors" in data
    assert "severity" in data["errors"]


def test_submit_bug_report_validation_failure_missing_required(client, test_db):
    """Test POST JSON with missing required fields returns 400."""
    json_data = {
        "reporter_name": "Test User",
        # Missing subject and description
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 400
    data = response.json()
    assert "errors" in data


def test_submit_bug_report_htmx_validation_error(client, test_db):
    """Test HTMX form submission with validation errors returns error fragment."""
    form_data = {
        "reporter_name": "Test User",
        "subject": "Sho",  # Too short
        "description": "Also short",
        "severity": "low",
    }

    response = client.post(
        "/admin/bug-report",
        data=form_data,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    # Should contain error indication
    assert "Validation Error" in response.text or "error" in response.text.lower()


def test_list_bug_reports_api(client, test_db):
    """Test GET /api/bug-reports returns list of reports."""
    # Create a few reports first
    for i in range(3):
        client.post(
            "/admin/api/bug-reports",
            json={
                "subject": f"Test Report {i}",
                "description": "This is a test description.",
                "severity": "low",
            },
        )

    response = client.get("/admin/api/bug-reports")

    assert response.status_code == 200
    data = response.json()
    assert "reports" in data
    assert len(data["reports"]) >= 3


def test_submit_bug_report_with_metadata(client, test_db):
    """Test POST JSON with metadata stores it correctly."""
    json_data = {
        "subject": "Bug with metadata",
        "description": "This bug has extra metadata.",
        "severity": "medium",
        "metadata": {"browser": "Chrome", "version": "1.0.0"},
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 201
    data = response.json()

    # Verify metadata was stored
    with get_db() as conn:
        row = conn.execute(
            "SELECT metadata FROM bug_reports WHERE id = ?",
            (data["id"],)
        ).fetchone()

        assert row is not None
        assert row["metadata"] is not None
        assert "Chrome" in row["metadata"]


def test_submit_bug_report_optional_fields(client, test_db):
    """Test POST with only required fields succeeds."""
    json_data = {
        "subject": "Minimal Bug Report",
        "description": "Only required fields provided.",
        "severity": "low",
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 201

    # Verify optional fields are null
    with get_db() as conn:
        row = conn.execute(
            "SELECT reporter_name, reporter_email FROM bug_reports WHERE subject = ?",
            ("Minimal Bug Report",)
        ).fetchone()

        assert row is not None
        assert row["reporter_name"] is None
        assert row["reporter_email"] is None


def test_submit_bug_report_default_severity(client, test_db):
    """Test POST without severity defaults to 'low'."""
    json_data = {
        "subject": "Bug without severity",
        "description": "No severity specified.",
    }

    response = client.post(
        "/admin/api/bug-reports",
        json=json_data,
    )

    assert response.status_code == 201

    with get_db() as conn:
        row = conn.execute(
            "SELECT severity FROM bug_reports WHERE subject = ?",
            ("Bug without severity",)
        ).fetchone()

        assert row is not None
        assert row["severity"] == "low"

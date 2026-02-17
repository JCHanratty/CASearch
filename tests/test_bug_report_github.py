import json

import pytest

from app.settings import settings


def test_create_bug_report_api_calls_github(monkeypatch, client, test_db):
    # Enable GitHub integration and set dummy repo/token
    monkeypatch.setattr(settings, "BUGREPORT_CREATE_ISSUE", True)
    monkeypatch.setattr(settings, "BUGREPORT_GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(settings, "BUGREPORT_GITHUB_TOKEN", "token123")
    # Also ensure the diagnostics module sees the same flag values
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_CREATE_ISSUE", True, raising=False)
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_GITHUB_REPO", "owner/repo", raising=False)
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_GITHUB_TOKEN", "token123", raising=False)

    called = {}

    def fake_create_issue(subject, description, severity, reporter_name, reporter_email, metadata=None):
        called['subject'] = subject
        return {"html_url": "https://github.com/owner/repo/issues/1", "id": 1}

    # Ensure diagnostics module uses the patched function reference
    monkeypatch.setattr("app.routes.diagnostics.bug_report_service.create_github_issue", fake_create_issue)

    payload = {
        "subject": "Test Issue",
        "description": "This is a test bug report created during tests.",
        "severity": "low",
    }

    resp = client.post("/admin/api/bug-reports", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "created"
    assert "issue_url" in data and data["issue_url"].startswith("https://github.com/")
    assert called.get('subject') == payload['subject']


def test_htmx_form_includes_issue_info(monkeypatch, client, test_db):
    monkeypatch.setattr(settings, "BUGREPORT_CREATE_ISSUE", True)
    monkeypatch.setattr(settings, "BUGREPORT_GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(settings, "BUGREPORT_GITHUB_TOKEN", "token123")
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_CREATE_ISSUE", True, raising=False)
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_GITHUB_REPO", "owner/repo", raising=False)
    monkeypatch.setattr("app.routes.diagnostics.settings.BUGREPORT_GITHUB_TOKEN", "token123", raising=False)

    def fake_create_issue(subject, description, severity, reporter_name, reporter_email, metadata=None):
        return {"html_url": "https://github.com/owner/repo/issues/2", "id": 2}

    monkeypatch.setattr("app.routes.diagnostics.bug_report_service.create_github_issue", fake_create_issue)

    headers = {"HX-Request": "true"}
    data = {
        "reporter_name": "Tester",
        "reporter_email": "test@example.com",
        "subject": "HTMX Issue",
        "description": "Testing HTMX form submission for GitHub issues.",
        "severity": "low",
    }

    resp = client.post("/admin/bug-report", data=data, headers=headers)
    assert resp.status_code == 200
    text = resp.text
    assert "issue" in text or "github.com/owner/repo/issues/2" in text

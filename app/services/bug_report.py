"""Bug report helpers, including optional GitHub issue creation."""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from app.settings import settings


def _github_api_request(url: str, data: dict, token: str) -> dict:
    """POST JSON to GitHub API and return parsed JSON response.

    Raises Exception on non-2xx responses.
    """
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "contract-dashboard")
    req.add_header("Authorization", f"token {token}")

    with urllib.request.urlopen(req, timeout=15) as resp:
        resp_body = resp.read()
        try:
            return json.loads(resp_body.decode("utf-8"))
        except Exception:
            return {"raw": resp_body.decode("utf-8", errors="ignore")}


def create_github_issue(
    subject: str,
    description: str,
    severity: str = "low",
    reporter_name: Optional[str] = None,
    reporter_email: Optional[str] = None,
    metadata: Optional[str] = None,
) -> Optional[dict]:
    """Create a GitHub issue for a bug report when configured.

    Returns the created issue JSON on success, or None if not configured.
    """
    if not settings.BUGREPORT_CREATE_ISSUE:
        return None

    repo = settings.BUGREPORT_GITHUB_REPO
    token = settings.BUGREPORT_GITHUB_TOKEN
    if not repo or not token:
        return None

    url = f"https://api.github.com/repos/{repo}/issues"

    body_lines = []
    if reporter_name:
        body_lines.append(f"**Reporter:** {reporter_name}")
    if reporter_email:
        body_lines.append(f"**Email:** {reporter_email}")
    body_lines.append(f"**Severity:** {severity}")
    body_lines.append("")
    body_lines.append(description)
    if metadata:
        body_lines.append("")
        body_lines.append("**Metadata:**")
        body_lines.append(metadata)

    issue = {
        "title": f"[Bug Report] {subject}",
        "body": "\n".join(body_lines),
        "labels": ["bug-report", severity],
    }

    try:
        return _github_api_request(url, issue, token)
    except Exception:
        # Swallow network errors; caller should handle missing issue info
        return None

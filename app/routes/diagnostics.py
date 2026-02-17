"""Diagnostics routes - system health and configuration."""

import json
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from typing import Optional

from app.db import get_db_stats, get_db
from app.services.search import get_fts_sync_status, rebuild_fts_index
from app.services import bug_report as bug_report_service
from app.services.synonyms import (
    get_all_synonyms,
    get_custom_synonyms_only,
    get_builtin_synonyms,
)
from app.settings import settings
from app.templates import templates
from app.version import __version__ as APP_VERSION

router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """Show current configuration (read-only)."""
    config = {
        "AGREEMENTS_DIR": str(settings.AGREEMENTS_DIR),
        "DATABASE_PATH": str(settings.DATABASE_PATH),
        "CLAUDE_MODEL": settings.CLAUDE_MODEL,
        "MAX_RETRIEVAL_RESULTS": settings.MAX_RETRIEVAL_RESULTS,
        "ANTHROPIC_API_KEY": "***" + settings.ANTHROPIC_API_KEY[-4:] if settings.ANTHROPIC_API_KEY else "Not set",
    }

    fts_status = get_fts_sync_status()

    # Get synonym data
    builtin = get_builtin_synonyms()
    custom = get_custom_synonyms_only()
    merged = get_all_synonyms()

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "config": config,
            "stats": get_db_stats(),
            "fts_status": fts_status,
            "page_title": "Diagnostics",
            # Synonym data
            "builtin_synonyms": builtin,
            "custom_synonyms": custom,
            "merged_synonyms": merged,
            "builtin_count": len(builtin),
            "custom_count": len(custom),
            "total_count": len(merged),
        }
    )


@router.post("/rebuild-fts", response_class=HTMLResponse)
async def rebuild_fts(request: Request):
    """Rebuild the FTS search index."""
    result = rebuild_fts_index()
    fts_status = get_fts_sync_status()

    config = {
        "AGREEMENTS_DIR": str(settings.AGREEMENTS_DIR),
        "DATABASE_PATH": str(settings.DATABASE_PATH),
        "CLAUDE_MODEL": settings.CLAUDE_MODEL,
        "MAX_RETRIEVAL_RESULTS": settings.MAX_RETRIEVAL_RESULTS,
        "ANTHROPIC_API_KEY": "***" + settings.ANTHROPIC_API_KEY[-4:] if settings.ANTHROPIC_API_KEY else "Not set",
    }

    # Get synonym data
    builtin = get_builtin_synonyms()
    custom = get_custom_synonyms_only()
    merged = get_all_synonyms()

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "config": config,
            "stats": get_db_stats(),
            "fts_status": fts_status,
            "rebuild_result": result,
            "page_title": "Diagnostics",
            # Synonym data
            "builtin_synonyms": builtin,
            "custom_synonyms": custom,
            "merged_synonyms": merged,
            "builtin_count": len(builtin),
            "custom_count": len(custom),
            "total_count": len(merged),
        }
    )


# --- JSON API Endpoints ---

@router.get("/health")
async def health_check():
    """Health check endpoint returning JSON status."""
    try:
        db_stats = get_db_stats()
        return JSONResponse(
            content={
                "status": "ok",
                "db": db_stats,
                "version": APP_VERSION,
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(e),
                "version": APP_VERSION,
            }
        )


@router.get("/check-update")
async def check_update(request: Request):
    """Manually re-check for app updates."""
    from app.services.update_service import check_for_update
    update_info = check_for_update(APP_VERSION)
    request.app.state.update_info = update_info
    return JSONResponse(content=update_info)


@router.get("/fts-status")
async def fts_status_json():
    """Get FTS sync status as JSON."""
    try:
        fts_status = get_fts_sync_status()
        return JSONResponse(content=fts_status)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@router.post("/rebuild-fts-json")
async def rebuild_fts_json():
    """Rebuild FTS index and return result as JSON."""
    try:
        result = rebuild_fts_index()
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "rebuilt": False}
        )


# --- Bug Report Endpoints ---

VALID_SEVERITIES = ["low", "medium", "high", "critical"]


def validate_bug_report(
    subject: str,
    description: str,
    severity: str,
) -> dict:
    """Validate bug report fields. Returns dict with errors if any."""
    errors = {}

    if not subject or len(subject.strip()) < 5:
        errors["subject"] = "Subject must be at least 5 characters"

    if not description or len(description.strip()) < 10:
        errors["description"] = "Description must be at least 10 characters"

    if severity not in VALID_SEVERITIES:
        errors["severity"] = f"Severity must be one of: {', '.join(VALID_SEVERITIES)}"

    return errors


def create_bug_report(
    reporter_name: Optional[str],
    reporter_email: Optional[str],
    subject: str,
    description: str,
    severity: str,
    metadata: Optional[str] = None,
) -> int:
    """Insert bug report into database and return the ID."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO bug_reports
               (reporter_name, reporter_email, subject, description, severity, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (reporter_name, reporter_email, subject.strip(), description.strip(), severity, metadata)
        )
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0]


@router.get("/bug-report/form", response_class=HTMLResponse)
async def bug_report_form(request: Request):
    """Return the bug report modal form."""
    return templates.TemplateResponse(
        "components/bug_report_modal.html",
        {"request": request}
    )


@router.post("/bug-report", response_class=HTMLResponse)
async def submit_bug_report(
    request: Request,
    reporter_name: str = Form(""),
    reporter_email: str = Form(""),
    subject: str = Form(""),
    description: str = Form(""),
    severity: str = Form("low"),
):
    """Handle bug report form submission (HTMX or regular)."""
    errors = validate_bug_report(subject, description, severity)

    if errors:
        # Return error fragment for HTMX
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                "components/bug_report_result.html",
                {"request": request, "success": False, "errors": errors}
            )
        # For non-HTMX, redirect back with error
        return RedirectResponse(url="/admin/config", status_code=303)

    # Create the bug report
    report_id = create_bug_report(
        reporter_name=reporter_name or None,
        reporter_email=reporter_email or None,
        subject=subject,
        description=description,
        severity=severity,
    )

    # Optionally create a GitHub Issue for tracking
    issue_info = None
    if settings.BUGREPORT_CREATE_ISSUE and settings.BUGREPORT_GITHUB_REPO and settings.BUGREPORT_GITHUB_TOKEN:
        try:
            issue_info = bug_report_service.create_github_issue(
                subject=subject,
                description=description,
                severity=severity,
                reporter_name=reporter_name or None,
                reporter_email=reporter_email or None,
                metadata=None,
            )
        except Exception:
            issue_info = None

    # Return success fragment for HTMX
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/bug_report_result.html",
            {"request": request, "success": True, "report_id": report_id, "issue": issue_info}
        )

    # For non-HTMX, redirect back
    return RedirectResponse(url="/admin/config", status_code=303)


@router.post("/api/bug-reports")
async def create_bug_report_api(request: Request):
    """JSON API endpoint for creating bug reports."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON"}
        )

    subject = body.get("subject", "")
    description = body.get("description", "")
    severity = body.get("severity", "low")
    reporter_name = body.get("reporter_name")
    reporter_email = body.get("reporter_email")
    metadata = body.get("metadata")

    # Validate
    errors = validate_bug_report(subject, description, severity)
    if errors:
        return JSONResponse(
            status_code=400,
            content={"errors": errors}
        )

    # Create
    metadata_str = json.dumps(metadata) if metadata else None
    report_id = create_bug_report(
        reporter_name=reporter_name,
        reporter_email=reporter_email,
        subject=subject,
        description=description,
        severity=severity,
        metadata=metadata_str,
    )

    # Optionally create GitHub issue for API submissions
    issue_info = None
    if settings.BUGREPORT_CREATE_ISSUE and settings.BUGREPORT_GITHUB_REPO and settings.BUGREPORT_GITHUB_TOKEN:
        try:
            issue_info = bug_report_service.create_github_issue(
                subject=subject,
                description=description,
                severity=severity,
                reporter_name=reporter_name,
                reporter_email=reporter_email,
                metadata=metadata_str,
            )
        except Exception:
            issue_info = None

    resp = {"id": report_id, "status": "created"}
    if issue_info is not None:
        # Include GitHub issue URL when available
        resp["issue_url"] = issue_info.get("html_url") or issue_info.get("url")

    return JSONResponse(
        status_code=201,
        content=resp
    )


@router.get("/api/bug-reports")
async def list_bug_reports_api():
    """JSON API endpoint for listing bug reports."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, reporter_name, reporter_email, subject, severity, status, created_at
               FROM bug_reports ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()

        reports = [
            {
                "id": r["id"],
                "reporter_name": r["reporter_name"],
                "reporter_email": r["reporter_email"],
                "subject": r["subject"],
                "severity": r["severity"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    return JSONResponse(content={"reports": reports})

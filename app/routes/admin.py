"""Admin routes - login, document management, publishing."""

import json
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Request as FastAPIRequest, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from app.db import get_db
from app.services.auth import (
    verify_password,
    create_session_token,
    verify_session,
    admin_enabled,
    SESSION_COOKIE,
)
from app.services.file_scanner import scan_agreements, get_all_files, get_file_by_id
from app.services.indexer import index_file
from app.settings import settings
from app.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: FastAPIRequest):
    """Admin login page."""
    if not admin_enabled():
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "page_title": "Admin Login",
             "error": "Admin access is not configured. Set ADMIN_PASSWORD in .env to enable."},
        )
    if verify_session(request):
        return RedirectResponse("/admin/panel", status_code=303)
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "page_title": "Admin Login", "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: FastAPIRequest, password: str = Form(...)):
    """Verify password and set session cookie."""
    if verify_password(password):
        token = create_session_token()
        response = RedirectResponse("/admin/panel", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=86400,
        )
        return response

    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "page_title": "Admin Login", "error": "Invalid password."},
        status_code=401,
    )


@router.post("/logout")
async def logout(request: FastAPIRequest):
    """Clear session and redirect home."""
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

@router.get("/panel", response_class=HTMLResponse)
async def admin_panel(request: FastAPIRequest):
    """Admin dashboard — upload, manage, publish."""
    if not verify_session(request):
        return RedirectResponse("/admin/login", status_code=303)

    documents = get_all_files()
    index_version = _read_index_version()

    return templates.TemplateResponse(
        "admin_panel.html",
        {
            "request": request,
            "page_title": "Admin Panel",
            "documents": documents,
            "index_version": index_version,
            "github_token_set": bool(settings.GITHUB_TOKEN),
        },
    )


# ---------------------------------------------------------------------------
# Document management (admin only)
# ---------------------------------------------------------------------------

@router.post("/upload", response_class=HTMLResponse)
async def upload_documents(
    request: FastAPIRequest,
    files: list[UploadFile] = File(...),
):
    """Upload PDF files to data/agreements/."""
    if not verify_session(request):
        return HTMLResponse('<p class="text-red-400 text-sm">Admin login required.</p>', status_code=403)

    uploaded = []
    errors = []

    for file in files:
        if not file.filename:
            continue
        if not file.filename.lower().endswith(".pdf"):
            errors.append(f"{file.filename}: Not a PDF file")
            continue

        content = await file.read()
        if len(content) > 50 * 1024 * 1024:  # 50MB limit
            errors.append(f"{file.filename}: File too large (max 50MB)")
            continue
        if len(content) == 0:
            errors.append(f"{file.filename}: Empty file")
            continue

        dest = settings.AGREEMENTS_DIR / file.filename
        dest.write_bytes(content)
        uploaded.append(file.filename)

    # Auto-scan after upload
    scan_results = None
    if uploaded:
        scan_results = scan_agreements()

    documents = get_all_files()

    return templates.TemplateResponse(
        "components/admin_upload_result.html",
        {
            "request": request,
            "uploaded": uploaded,
            "errors": errors,
            "scan_results": scan_results,
            "documents": documents,
        },
    )


@router.post("/documents/{file_id}/delete", response_class=HTMLResponse)
async def delete_document(request: FastAPIRequest, file_id: int):
    """Delete a document — remove from disk and database."""
    if not verify_session(request):
        return HTMLResponse('<p class="text-red-400 text-sm">Admin login required.</p>', status_code=403)

    doc = get_file_by_id(file_id)
    if not doc:
        return HTMLResponse('<p class="text-red-400 text-sm">Document not found.</p>', status_code=404)

    # Delete PDF from disk
    pdf_path = settings.AGREEMENTS_DIR / doc.path
    if pdf_path.exists():
        pdf_path.unlink()

    # Delete from database (CASCADE handles related tables)
    with get_db() as conn:
        # Delete FTS entries first (no CASCADE on virtual tables)
        conn.execute("DELETE FROM page_fts WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM chunk_fts WHERE file_id IN (SELECT id FROM document_chunks WHERE file_id = ?)", (file_id,))
        # Delete file record (CASCADE handles pdf_pages, document_chunks, document_tables)
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    # Return empty string to remove the row via HTMX
    return HTMLResponse("")


@router.post("/scan-and-index", response_class=HTMLResponse)
async def scan_and_index(request: FastAPIRequest):
    """Scan for new files and index all pending."""
    if not verify_session(request):
        return HTMLResponse('<p class="text-red-400 text-sm">Admin login required.</p>', status_code=403)

    scan_results = scan_agreements()

    documents = get_all_files()
    indexed = 0
    errors = 0
    for doc in documents:
        if doc.status == "pending":
            try:
                index_file(doc.id)
                indexed += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", doc.filename, e)
                errors += 1

    documents = get_all_files()

    return templates.TemplateResponse(
        "components/admin_documents_table.html",
        {
            "request": request,
            "documents": documents,
            "indexed_count": indexed,
            "error_count": errors,
        },
    )


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

@router.post("/publish", response_class=HTMLResponse)
async def publish_index(request: FastAPIRequest):
    """Package app.db and upload to GitHub Releases."""
    if not verify_session(request):
        return HTMLResponse('<p class="text-red-400 text-sm">Admin login required.</p>', status_code=403)

    if not settings.GITHUB_TOKEN:
        return HTMLResponse(
            '<div class="p-3 bg-red-900/20 border border-red-800/50 rounded-lg">'
            '<p class="text-sm text-red-400">GITHUB_TOKEN not set in .env</p></div>'
        )

    try:
        # Bump index version
        version = _bump_index_version()

        # Package app.db into zip
        db_path = settings.DATABASE_PATH
        staging_dir = Path("data/publish_staging")
        staging_dir.mkdir(parents=True, exist_ok=True)

        zip_name = f"index-v{version}.zip"
        zip_path = staging_dir / zip_name

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, "app.db")
            metadata = {"version": version, "format": "app-db", "schema_version": 8}
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        # Upload to GitHub Release
        result = _publish_to_github(zip_path, version)

        # Cleanup staging
        shutil.rmtree(staging_dir, ignore_errors=True)

        return HTMLResponse(
            f'<div class="p-3 bg-green-900/20 border border-green-700/50 rounded-lg">'
            f'<p class="text-sm text-green-400">Published index v{version} to GitHub.</p>'
            f'<p class="text-xs text-green-400/70 mt-1">Release: {result.get("tag", "")}</p>'
            f'</div>'
        )

    except Exception as e:
        logger.exception("Publish failed")
        return HTMLResponse(
            f'<div class="p-3 bg-red-900/20 border border-red-800/50 rounded-lg">'
            f'<p class="text-sm text-red-400">Publish failed: {e}</p></div>'
        )


# ---------------------------------------------------------------------------
# Index update endpoints (for user-facing .exe)
# ---------------------------------------------------------------------------

@router.get("/update-modal", response_class=HTMLResponse)
async def update_modal(request: FastAPIRequest, version: str = ""):
    """Return the index update modal HTML fragment."""
    return templates.TemplateResponse(
        "components/update_modal.html",
        {"request": request, "version": version},
    )


@router.get("/app-update-modal", response_class=HTMLResponse)
async def app_update_modal(request: FastAPIRequest):
    """Return the app update modal HTML fragment."""
    update = getattr(request.app.state, "update_info", None) or {}
    return templates.TemplateResponse(
        "components/app_update_modal.html",
        {
            "request": request,
            "version": update.get("latest_version", ""),
            "current_version": update.get("current_version", ""),
            "release_notes": update.get("release_notes", ""),
            "download_url": update.get("download_url") or update.get("html_url", ""),
        },
    )


@router.get("/check-index-update")
async def check_index_update(request: FastAPIRequest):
    """Check if a pending index update has been downloaded."""
    pending = getattr(request.app.state, "pending_index_update", None)
    return JSONResponse({
        "pending": pending is not None,
        "version": pending.get("version") if pending else None,
    })


@router.post("/apply-update")
async def apply_update(request: FastAPIRequest):
    """Trigger app shutdown so user can restart with new data."""
    # The pending update will be applied on next startup
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="text-center py-4">'
            '<p class="text-surface-200 text-sm">Shutting down... Please reopen the application.</p>'
            '</div>'
        )

    # Schedule shutdown
    import threading
    def _shutdown():
        import time
        time.sleep(1)
        os._exit(0)
    threading.Thread(target=_shutdown, daemon=True).start()

    return JSONResponse({"status": "shutting_down"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_index_version() -> str:
    """Read current index version from file."""
    version_file = Path("data/index_version.txt")
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"


def _bump_index_version() -> str:
    """Increment minor version and save."""
    current = _read_index_version()
    from app.services.updater import parse_version
    parts = list(parse_version(current))
    parts[2] += 1  # Bump patch
    new_version = ".".join(str(p) for p in parts)

    version_file = Path("data/index_version.txt")
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(new_version)
    return new_version


def _publish_to_github(zip_path: Path, version: str) -> dict:
    """Upload zip to GitHub Releases. Reuses logic from tools/build_index.py."""
    tag = f"index-v{version}"
    repo = settings.GITHUB_REPO
    token = settings.GITHUB_TOKEN
    api_base = f"https://api.github.com/repos/{repo}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if release exists, create if not
    try:
        req = Request(f"{api_base}/releases/tags/{tag}", headers=headers)
        with urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read().decode())
            release_id = release["id"]
    except HTTPError as e:
        if e.code == 404:
            release_data = json.dumps({
                "tag_name": tag,
                "name": f"Index {tag}",
                "body": f"Pre-built document index version {version}",
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

    # Upload zip asset
    upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
    asset_name = zip_path.name

    with open(zip_path, "rb") as f:
        data = f.read()

    req = Request(
        f"{upload_url}?name={asset_name}",
        data=data,
        headers={
            **headers,
            "Content-Type": "application/zip",
            "Content-Length": str(len(data)),
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=300) as resp:
            json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code != 422:  # 422 = asset already exists
            raise

    return {"tag": tag, "release_id": release_id}

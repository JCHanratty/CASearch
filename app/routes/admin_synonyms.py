"""Admin routes for synonym management."""

import json
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.templates import templates
from app.services.synonyms import (
    BUILTIN_SYNONYMS,
    get_all_synonyms,
    get_custom_synonyms_only,
    get_builtin_synonyms,
    save_custom_synonyms_to_db,
    delete_custom_synonym,
    reload_synonyms,
    parse_uploaded_synonyms,
)

router = APIRouter()


@router.get("/synonyms", response_class=HTMLResponse)
async def synonyms_admin_page(request: Request):
    """
    Synonym Library page - add/view/delete synonyms.
    """
    builtin = get_builtin_synonyms()
    custom = get_custom_synonyms_only()
    merged = get_all_synonyms()

    return templates.TemplateResponse(
        "synonym_library.html",
        {
            "request": request,
            "page_title": "Synonym Library",
            "builtin_synonyms": builtin,
            "custom_synonyms": custom,
            "merged_synonyms": merged,
            "builtin_count": len(builtin),
            "custom_count": len(custom),
            "total_count": len(merged),
        }
    )


@router.post("/synonyms/upload", response_class=HTMLResponse)
async def upload_synonyms(
    request: Request,
    file: UploadFile = File(...),
    replace_all: bool = Form(False),
):
    """
    Handle CSV/JSON file upload for synonyms.

    Accepts both regular form submission and HTMX requests.
    """
    errors = []
    success_message = None
    preview_data = None

    try:
        # Validate file
        if not file.filename:
            errors.append("No file selected")
        elif not file.filename.lower().endswith(('.csv', '.json')):
            errors.append("File must be .csv or .json format")
        else:
            # Read and parse file
            content = await file.read()
            if len(content) > 1024 * 1024:  # 1MB limit
                errors.append("File too large (max 1MB)")
            else:
                try:
                    parsed = parse_uploaded_synonyms(content, file.filename)
                    if not parsed:
                        errors.append("No valid synonyms found in file")
                    else:
                        # Check if this is a preview request
                        if request.headers.get("HX-Trigger") == "preview-btn":
                            preview_data = parsed
                        else:
                            # Actually save the synonyms
                            count = save_custom_synonyms_to_db(parsed, replace=replace_all)
                            success_message = f"Successfully imported {count} synonym entries"

                except ValueError as e:
                    errors.append(str(e))

    except Exception as e:
        errors.append(f"Upload failed: {str(e)}")

    # Get current state
    builtin = get_builtin_synonyms()
    custom = get_custom_synonyms_only()
    merged = get_all_synonyms()

    # Check if HTMX request
    if request.headers.get("HX-Request"):
        # Return just the results section for HTMX
        return templates.TemplateResponse(
            "components/synonyms_upload_result.html",
            {
                "request": request,
                "errors": errors,
                "success_message": success_message,
                "preview_data": preview_data,
                "custom_synonyms": custom,
                "custom_count": len(custom),
            }
        )

    # Full page response for non-HTMX
    return templates.TemplateResponse(
        "admin_synonyms.html",
        {
            "request": request,
            "page_title": "Synonym Management",
            "builtin_synonyms": builtin,
            "custom_synonyms": custom,
            "merged_synonyms": merged,
            "builtin_count": len(builtin),
            "custom_count": len(custom),
            "total_count": len(merged),
            "errors": errors,
            "success_message": success_message,
            "preview_data": preview_data,
        }
    )


@router.post("/synonyms/preview", response_class=HTMLResponse)
async def preview_synonyms(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Preview uploaded synonyms without applying them.
    Returns HTML fragment for HTMX.
    """
    errors = []
    preview_data = None

    try:
        if not file.filename:
            errors.append("No file selected")
        elif not file.filename.lower().endswith(('.csv', '.json')):
            errors.append("File must be .csv or .json format")
        else:
            content = await file.read()
            if len(content) > 1024 * 1024:
                errors.append("File too large (max 1MB)")
            else:
                try:
                    preview_data = parse_uploaded_synonyms(content, file.filename)
                    if not preview_data:
                        errors.append("No valid synonyms found in file")
                except ValueError as e:
                    errors.append(str(e))

    except Exception as e:
        errors.append(f"Preview failed: {str(e)}")

    return templates.TemplateResponse(
        "components/synonyms_preview.html",
        {
            "request": request,
            "errors": errors,
            "preview_data": preview_data,
            "filename": file.filename if file else None,
        }
    )


@router.get("/synonyms/download")
async def download_synonyms(include_builtin: bool = False):
    """
    Download current synonyms as JSON file.

    Args:
        include_builtin: If True, include built-in synonyms. Otherwise only custom.
    """
    if include_builtin:
        data = get_all_synonyms()
        filename = "all_synonyms.json"
    else:
        data = get_custom_synonyms_only()
        filename = "custom_synonyms.json"

    content = json.dumps(data, indent=2, ensure_ascii=False)

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/synonyms/reload", response_class=HTMLResponse)
async def reload_synonyms_endpoint(request: Request):
    """
    Reload synonyms from database.
    Useful after manual DB edits or to refresh cache.
    """
    merged = reload_synonyms()
    custom = get_custom_synonyms_only()
    builtin = get_builtin_synonyms()

    # HTMX fragment response
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/synonyms_status.html",
            {
                "request": request,
                "success_message": "Synonyms reloaded successfully",
                "custom_count": len(custom),
                "total_count": len(merged),
            }
        )

    # Full page for non-HTMX
    return templates.TemplateResponse(
        "admin_synonyms.html",
        {
            "request": request,
            "page_title": "Synonym Management",
            "builtin_synonyms": builtin,
            "custom_synonyms": custom,
            "merged_synonyms": merged,
            "builtin_count": len(builtin),
            "custom_count": len(custom),
            "total_count": len(merged),
            "success_message": "Synonyms reloaded successfully",
        }
    )


@router.delete("/synonyms/{canonical_term}", response_class=HTMLResponse)
async def delete_synonym(request: Request, canonical_term: str):
    """
    Delete a custom synonym entry.
    """
    deleted = delete_custom_synonym(canonical_term)
    custom = get_custom_synonyms_only()

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/synonyms_custom_list.html",
            {
                "request": request,
                "custom_synonyms": custom,
                "custom_count": len(custom),
                "success_message": f"Deleted '{canonical_term}'" if deleted else None,
                "error_message": f"'{canonical_term}' not found" if not deleted else None,
            }
        )

    # Redirect for non-HTMX
    return HTMLResponse(
        status_code=303,
        headers={"Location": "/admin/synonyms"}
    )


@router.post("/synonyms/add", response_class=HTMLResponse)
async def add_synonym(
    request: Request,
    canonical_term: str = Form(...),
    synonyms: str = Form(...),
):
    """
    Add a single synonym entry via form.
    """
    errors = []
    success_message = None

    canonical = canonical_term.strip().lower()
    syns_list = [s.strip().lower() for s in synonyms.split(',') if s.strip()]

    if not canonical:
        errors.append("Canonical term is required")
    elif not syns_list:
        errors.append("At least one synonym is required")
    else:
        try:
            count = save_custom_synonyms_to_db({canonical: syns_list}, replace=False)
            success_message = f"Added/updated '{canonical}' with {len(syns_list)} synonym(s)"
        except Exception as e:
            errors.append(f"Failed to save: {str(e)}")

    custom = get_custom_synonyms_only()

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/synonyms_custom_list.html",
            {
                "request": request,
                "custom_synonyms": custom,
                "custom_count": len(custom),
                "success_message": success_message,
                "errors": errors,
            }
        )

    # Redirect for non-HTMX
    return HTMLResponse(
        status_code=303,
        headers={"Location": "/admin/synonyms"}
    )


# --- JSON API Endpoints ---

@router.get("/api/synonyms")
async def get_synonyms_api(include_builtin: bool = True):
    """JSON API to get all synonyms."""
    if include_builtin:
        return JSONResponse(content=get_all_synonyms())
    else:
        return JSONResponse(content=get_custom_synonyms_only())


@router.post("/api/synonyms")
async def set_synonyms_api(request: Request, replace: bool = False):
    """JSON API to set synonyms."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "Body must be a JSON object"}
            )

        count = save_custom_synonyms_to_db(body, replace=replace)
        return JSONResponse(content={"saved": count, "replace": replace})

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e)}
        )


@router.delete("/api/synonyms/{canonical_term}")
async def delete_synonym_api(canonical_term: str):
    """JSON API to delete a synonym."""
    deleted = delete_custom_synonym(canonical_term)
    return JSONResponse(content={"deleted": deleted, "term": canonical_term})

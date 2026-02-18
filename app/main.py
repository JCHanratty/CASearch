"""Contract Dashboard - FastAPI Application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import shutil
import sqlite3

from app.db import init_db
from app.settings import settings
from app.version import __version__


def _apply_pending_update():
    """Apply a staged index update before the database opens."""
    pending_db = Path("data/pending_update/app.db")
    if not pending_db.exists():
        return

    current_db = settings.DATABASE_PATH
    print("[Startup] Applying pending index update...")

    try:
        # Preserve local-only data from current db
        preserved = {"bug_reports": [], "custom_synonyms": []}
        if current_db.exists():
            conn = sqlite3.connect(str(current_db))
            conn.row_factory = sqlite3.Row
            for table in preserved:
                try:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                    preserved[table] = [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    pass  # Table doesn't exist yet
            conn.close()

            # Backup current db
            backup = current_db.with_suffix(".db.backup")
            shutil.copy2(current_db, backup)

        # Swap in the new database
        shutil.move(str(pending_db), str(current_db))

        # Re-insert preserved data
        if any(preserved.values()):
            conn = sqlite3.connect(str(current_db))
            for table, rows in preserved.items():
                if not rows:
                    continue
                cols = list(rows[0].keys())
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                for row in rows:
                    try:
                        conn.execute(
                            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
                            [row[c] for c in cols],
                        )
                    except sqlite3.OperationalError:
                        pass
            conn.commit()
            conn.close()

        # Update version file
        metadata_file = Path("data/pending_update/metadata.json")
        if metadata_file.exists():
            import json
            meta = json.loads(metadata_file.read_text())
            version_file = Path("data/index_version.txt")
            version_file.write_text(meta.get("version", "0.0.0"))

        # Cleanup staging
        shutil.rmtree("data/pending_update", ignore_errors=True)
        print("[Startup] Index update applied successfully")

    except Exception as e:
        print(f"[Startup] Failed to apply update: {e}")
        # Restore backup if available
        backup = current_db.with_suffix(".db.backup")
        if backup.exists():
            shutil.copy2(backup, current_db)
            print("[Startup] Restored database from backup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.AGREEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    # Apply pending index update before opening the database
    _apply_pending_update()

    init_db()

    # Check for index updates (non-blocking on failure)
    if settings.AUTO_UPDATE_ENABLED:
        try:
            from app.services.updater import async_ensure_latest_index
            await async_ensure_latest_index()
        except Exception as e:
            print(f"[Startup] Update check failed (non-fatal): {e}")

    # Check for app updates (non-blocking on failure)
    try:
        from app.services.update_service import check_for_update
        update_info = check_for_update(__version__)
        app.state.update_info = update_info
        if update_info.get("available"):
            print(f"[Startup] Update available: {update_info['latest_version']}")
        else:
            print(f"[Startup] App is up to date (v{__version__})")
    except Exception as e:
        app.state.update_info = {"available": False, "error": str(e)}
        print(f"[Startup] Update check failed (non-fatal): {e}")

    # Check for index updates in the background (non-blocking)
    app.state.pending_index_update = None
    try:
        from app.services.updater import check_for_index_update, download_index_to_staging
        index_status = check_for_index_update()
        if index_status.get("available"):
            print(f"[Startup] New index available: v{index_status['latest_version']}")
            staging_result = download_index_to_staging(index_status)
            if staging_result.get("downloaded"):
                app.state.pending_index_update = {
                    "version": index_status["latest_version"],
                }
                print(f"[Startup] Index update staged for restart")
    except Exception as e:
        print(f"[Startup] Index update check failed (non-fatal): {e}")

    yield
    # Shutdown (cleanup if needed)


app = FastAPI(
    title="Contract Dashboard",
    description="PDF collective agreement search and Q&A with citations",
    version=__version__,
    lifespan=lifespan,
)

# Static files
static_path = Path(__file__).parent.parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Include routers
from app.routes import dashboard, documents, search, qa, compare, matrix, diagnostics, tutorial, admin_synonyms, admin

app.include_router(dashboard.router)
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(qa.router, prefix="/qa", tags=["qa"])
app.include_router(compare.router, prefix="/compare", tags=["compare"])
app.include_router(matrix.router, prefix="/matrix", tags=["matrix"])
app.include_router(diagnostics.router, prefix="/admin", tags=["admin"])
app.include_router(admin_synonyms.router, prefix="/admin", tags=["admin"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(tutorial.router, prefix="/tutorial", tags=["tutorial"])

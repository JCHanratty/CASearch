"""Contract Dashboard - FastAPI Application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.settings import settings
from app.version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.AGREEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)
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
from app.routes import dashboard, documents, search, qa, compare, matrix, diagnostics, tutorial, admin_synonyms

app.include_router(dashboard.router)
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(qa.router, prefix="/qa", tags=["qa"])
app.include_router(compare.router, prefix="/compare", tags=["compare"])
app.include_router(matrix.router, prefix="/matrix", tags=["matrix"])
app.include_router(diagnostics.router, prefix="/admin", tags=["admin"])
app.include_router(admin_synonyms.router, prefix="/admin", tags=["admin"])
app.include_router(tutorial.router, prefix="/tutorial", tags=["tutorial"])

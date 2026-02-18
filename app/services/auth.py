"""Admin authentication service using signed session cookies."""

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.settings import settings

# Session cookie name
SESSION_COOKIE = "admin_session"
# Session max age: 24 hours
SESSION_MAX_AGE = 86400


def _get_secret() -> str:
    """Derive a signing secret from the admin password."""
    return hashlib.sha256(f"casearch-{settings.ADMIN_PASSWORD}".encode()).hexdigest()


def _get_serializer() -> URLSafeTimedSerializer:
    """Get the token serializer."""
    return URLSafeTimedSerializer(_get_secret())


def admin_enabled() -> bool:
    """Check if admin features are enabled (password is set)."""
    return bool(settings.ADMIN_PASSWORD)


def verify_password(password: str) -> bool:
    """Check a password against the configured admin password."""
    if not admin_enabled():
        return False
    return hmac.compare_digest(password, settings.ADMIN_PASSWORD)


def create_session_token() -> str:
    """Create a signed session token."""
    return _get_serializer().dumps({"role": "admin"})


def verify_session(request: Request) -> bool:
    """Check if the request has a valid admin session cookie."""
    if not admin_enabled():
        return False
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_admin(request: Request) -> bool:
    """Non-raising check for templates. Returns True if admin is logged in."""
    return verify_session(request)


async def require_admin(request: Request):
    """FastAPI dependency — redirects to login if not authenticated."""
    if not verify_session(request):
        # For HTMX requests, return 403 so the client can handle it
        if request.headers.get("HX-Request"):
            from fastapi.responses import HTMLResponse
            return HTMLResponse(
                '<p class="text-red-400 text-sm">Admin login required.</p>',
                status_code=403,
            )
        return RedirectResponse("/admin/login", status_code=303)
    return None

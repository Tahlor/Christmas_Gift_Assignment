"""Security utilities: authentication, CSRF, tokens, and rate limiting."""

import functools
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from flask import (
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from web.db import get_db


def verify_admin_password(provided_password: str) -> bool:
    """Constant-time verification of admin password against configured secret."""
    admin_password = current_app.config.get("ADMIN_PASSWORD", "")
    if not admin_password:
        return False
    if not provided_password:
        return False
    return hmac.compare_digest(provided_password.strip(), admin_password.strip())


def generate_family_token() -> str:
    """Generate high-entropy unpredictable token for family private links."""
    return secrets.token_urlsafe(32)


def get_csrf_token() -> str:
    """Retrieve or create session CSRF token."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def validate_csrf():
    """Validate CSRF token for state-modifying requests."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        expected = session.get("_csrf_token")
        if not expected or not token or not hmac.compare_digest(token, expected):
            abort(400, description="CSRF token missing or invalid. Please refresh the page and try again.")


def admin_required(view_func):
    """Decorator ensuring administrator authentication."""
    @functools.wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.admin_login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped_view


def check_login_rate_limit(ip_address: str, max_failures: int = 5, window_minutes: int = 15) -> bool:
    """Check if client IP is currently rate-limited due to excessive failed attempts.

    Returns True if allowed, False if blocked.
    """
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    row = db.execute(
        """
        SELECT COUNT(*) as failures
        FROM login_attempts
        WHERE ip_address = ? AND attempt_time >= ? AND success = 0
        """,
        (ip_address, cutoff)
    ).fetchone()

    failures = row["failures"] if row else 0
    return failures < max_failures


def record_login_attempt(ip_address: str, success: bool):
    """Record a login attempt in the database."""
    db = get_db()
    with db:
        db.execute(
            """
            INSERT INTO login_attempts (ip_address, attempt_time, success)
            VALUES (?, ?, ?)
            """,
            (ip_address, datetime.now(timezone.utc).isoformat(), 1 if success else 0)
        )


def apply_security_headers(response):
    """Apply strict browser security headers to all responses."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    return response

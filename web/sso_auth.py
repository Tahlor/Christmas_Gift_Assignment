"""Validate an existing Universal SSO cookie without an extra login click.

The cookie is verified by the existing local SSO service. Never trust a
client-provided identity header or decode the JWT without verifying its
signature. Christmas organizer access requires a separate explicit allowlist.
"""

import os
import re
import urllib.error
import urllib.request

from flask import request, session


def valid_sso_identity():
    """Return a whitelisted organizer's verified email, or None.

    This method MUST remain fail-closed. It neither authenticates public
    family links nor bypasses password login on SSO outages.
    """
    token = request.cookies.get("universal_sso_token", "")
    if not token or len(token) > 8192 or not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        return None

    allowed = {
        email.strip().lower()
        for email in os.environ.get("CHRISTMAS_ADMIN_SSO_USERS", "").split(",")
        if email.strip()
    }
    if not allowed:
        return None

    auth_request = urllib.request.Request(
        "http://127.0.0.1:5050/sso/auth-request",
        headers={
            "Cookie": "universal_sso_token=" + token,
            "X-Original-URI": "/hub/",
            "Host": "taylorarchibald.com",
        },
    )
    try:
        with urllib.request.urlopen(auth_request, timeout=3) as response:
            if response.status != 204:
                return None
            identity = (response.headers.get("X-Auth-Request-User", "") or "").strip().lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    return identity if identity in allowed else None


def auto_login_from_existing_sso():
    """Start a local organizer session after SSO verification, if permitted."""
    if session.get("is_admin"):
        return True

    # An explicit Christmas logout should not immediately log the user back
    # in with the still-valid shared SSO cookie.
    if session.get("skip_sso_auto"):
        return False

    identity = valid_sso_identity()
    if not identity:
        return False

    session.clear()
    session["is_admin"] = True
    session["sso_user"] = identity
    session.permanent = False
    return True

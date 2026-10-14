"""
Admin: /admin/network-check

A 10-second health check for the things the security layer depends on:
does the server see each visitor's REAL IP address (rate limits depend on it),
is the connection seen as https (Secure cookies depend on it), and which website
addresses are allowed to submit forms.
"""
import os

from flask import render_template, request

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.shared.security import client_ip


@admin_bp.route("/network-check")
@admin_required
def network_check():
    seen = {
        "Resolved client IP (what the rate limiter uses)": client_ip(),
        "remote_addr (after ProxyFix)": request.remote_addr,
        "X-Forwarded-For": request.headers.get("X-Forwarded-For", ""),
        "True-Client-IP": request.headers.get("True-Client-IP", ""),
        "CF-Connecting-IP": request.headers.get("CF-Connecting-IP", ""),
        "X-Forwarded-Proto": request.headers.get("X-Forwarded-Proto", ""),
        "Connection seen as https": "yes" if request.is_secure else "NO",
        "Host header": request.host,
        "Running on Render": "yes" if os.environ.get("RENDER") else "no",
    }
    return render_template(
        "admin_network_check.html",
        seen=seen,
        resolved=client_ip(),
        is_secure=request.is_secure,
        on_render=bool(os.environ.get("RENDER")),
    )

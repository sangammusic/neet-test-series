# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
"""
Security helpers (Phase 0).

Everything here is dependency-free on purpose (standard library + Flask only),
so a bad `pip install` on deploy can never take the security layer down.

What lives here
---------------
1. CSRF protection  -> `init_origin_guard(app)`
   Every state-changing request (POST/PUT/PATCH/DELETE) must come from OUR OWN
   site: the browser-supplied `Origin` (or, failing that, `Referer`) header has
   to match our host. A form on some other website can't fake that header.
   Together with the `SameSite=Lax` session cookie (set in app/__init__.py)
   this is the "verify origin with standard headers" defence recommended by
   OWASP -- and it needs NO changes in your 20+ templates, unlike hidden
   CSRF-token fields.

2. Rate limiting   -> `rl_blocked / rl_record / rl_hit / rl_clear`
   A tiny in-memory sliding window. Per gunicorn worker, so with 2 workers the
   real limit is up to 2x the number you pass. Resets on every deploy.

3. Cloudflare Turnstile (optional bot check) -> `verify_turnstile`
   Does nothing until you set TURNSTILE_SITE_KEY + TURNSTILE_SECRET_KEY.

4. Security headers + friendly error pages that never leak internals.
"""
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from urllib.parse import urlparse

from flask import jsonify, make_response, render_template, request

logger = logging.getLogger(__name__)


# ======================================================================
# Small request helpers
# ======================================================================

def client_ip():
    """
    The visitor's real IP address (used by the rate limiter).

    * On Render, every request first passes through Render's edge (Cloudflare), which
      sets the `True-Client-IP` / `CF-Connecting-IP` headers itself and overwrites any
      value a visitor tries to send. Render's own `X-Forwarded-For` list, on the other
      hand, only APPENDS to what the visitor sent and can be faked -- so on Render we
      read the Cloudflare headers first. (Render sets the RENDER environment variable.)
    * Anywhere else we use request.remote_addr, which app/__init__.py fixes up with
      ProxyFix (see TRUSTED_PROXY_COUNT).
    * CLIENT_IP_HEADER lets you name the header yourself if you use another host.

    Check what the server sees at  /admin/network-check  (admin only).
    """
    explicit = (os.environ.get("CLIENT_IP_HEADER") or "").strip()
    if explicit:
        names = [explicit]
    elif os.environ.get("RENDER"):
        names = ["True-Client-IP", "CF-Connecting-IP"]
    else:
        names = []
    for name in names:
        value = (request.headers.get(name) or "").split(",")[0].strip()
        if value:
            return value
    return request.remote_addr or "0.0.0.0"


def wants_json():
    """True when the caller is JavaScript (fetch/XHR) expecting JSON back."""
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return request.accept_mimetypes.best == "application/json"


# ======================================================================
# Friendly error responses (HTML for people, JSON for fetch())
# ======================================================================

_ERRORS = {
    400: ("Bad request", "That request didn't look right. Please go back and try again."),
    401: ("Please log in", "Log in to continue."),
    403: ("Access denied", "You don't have permission to open this page."),
    404: ("Page not found", "The page you're looking for doesn't exist or may have been moved."),
    405: ("Not allowed", "This page can't be opened that way."),
    413: ("File too large", "That file is too big. Please choose a smaller one."),
    429: ("Too many requests", "You're going a bit fast. Please wait a moment and try again."),
    500: ("Something went wrong", "That's on us, not you. Please try again in a minute."),
    502: ("Temporarily unavailable", "We're having a short hiccup. Please try again in a minute."),
    503: ("Temporarily unavailable", "We're having a short hiccup. Please try again in a minute."),
}


def error_response(code, message=None, retry_after=None):
    """Builds a clean error response. Never includes exception text."""
    title, default_message = _ERRORS.get(code, ("Error", "Something went wrong. Please try again."))
    text = message or default_message
    if wants_json():
        resp = jsonify({"ok": False, "error": text})
        resp.status_code = code
    else:
        resp = make_response(
            render_template("shared/error.html", code=code, title=title, message=text), code
        )
    if retry_after:
        resp.headers["Retry-After"] = str(int(retry_after))
    return resp


def minutes_text(seconds):
    """'about 3 minutes' style text for 'try again in ...' messages."""
    mins = max(1, int(round(seconds / 60.0)))
    return f"about {mins} minute{'s' if mins != 1 else ''}"


# ======================================================================
# 1. CSRF: origin check
# ======================================================================

_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _allowed_hosts():
    hosts = {request.host.lower()}
    fwd = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip().lower()
    if fwd:
        hosts.add(fwd)
    # Optional: extra hosts that may legitimately post to us, e.g.
    # ALLOWED_HOSTS=sangamstudy.in,www.sangamstudy.in
    for h in (os.environ.get("ALLOWED_HOSTS") or "").split(","):
        h = h.strip().lower()
        if h:
            hosts.add(h)
    return hosts


def _host_of(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _origin_ok():
    origin = request.headers.get("Origin")
    if origin and origin != "null":
        return _host_of(origin) in _allowed_hosts()
    referer = request.headers.get("Referer")
    if referer:
        return _host_of(referer) in _allowed_hosts()
    # A state-changing request with neither header is not a normal browser request.
    return False


def init_origin_guard(app, exempt_endpoints=()):
    """
    Registers the CSRF origin check on `app`.
    `exempt_endpoints`: endpoint names that are NOT called by browsers and have
    their own authentication (the Telegram webhook, which checks a secret header).
    """
    exempt = set(exempt_endpoints)

    @app.before_request
    def _origin_guard():
        if request.method in _SAFE_METHODS:
            return None
        if request.endpoint == "static" or request.endpoint in exempt:
            return None
        if _origin_ok():
            return None
        logger.warning(
            "Blocked cross-site %s %s (origin=%r, referer=%r)",
            request.method, request.path,
            (request.headers.get("Origin") or "")[:120],
            (request.headers.get("Referer") or "")[:120],
        )
        return error_response(
            403, "Your request could not be verified. Please reload the page and try again."
        )


# ======================================================================
# 2. Rate limiting (in-memory sliding window)
# ======================================================================

_LOCK = threading.Lock()
_EVENTS = {}            # "bucket:key" -> deque of monotonic timestamps
_MAX_KEYS = 20000
_FORGET_AFTER = 86400   # drop keys idle for a day


def _prune(now):
    dead = [k for k, dq in _EVENTS.items() if not dq or now - dq[-1] > _FORGET_AFTER]
    for k in dead:
        del _EVENTS[k]
    if len(_EVENTS) >= _MAX_KEYS:
        _EVENTS.clear()   # memory safety valve: forget everything rather than grow forever


def rl_blocked(bucket, key, limit, window):
    """
    Seconds until `key` may try again (0 = not blocked). Only LOOKS, never records.
    Use together with rl_record() when only FAILURES should count (logins).
    """
    now = time.monotonic()
    with _LOCK:
        dq = _EVENTS.get(f"{bucket}:{key}")
        if not dq:
            return 0
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return max(1, int(dq[0] + window - now) + 1)
        return 0


def rl_record(bucket, key, window):
    """Records one event for `key`."""
    now = time.monotonic()
    with _LOCK:
        k = f"{bucket}:{key}"
        dq = _EVENTS.get(k)
        if dq is None:
            if len(_EVENTS) >= _MAX_KEYS:
                _prune(now)
            dq = _EVENTS[k] = deque()
        dq.append(now)
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()


def rl_hit(bucket, key, limit, window):
    """Check AND record in one go. Returns seconds to wait (0 = allowed)."""
    wait = rl_blocked(bucket, key, limit, window)
    if wait:
        return wait
    rl_record(bucket, key, window)
    return 0


def rl_clear(bucket, key):
    with _LOCK:
        _EVENTS.pop(f"{bucket}:{key}", None)


def rl_reset_all():
    """Used by the tests."""
    with _LOCK:
        _EVENTS.clear()


# ======================================================================
# 3. Cloudflare Turnstile (optional)
# ======================================================================

def turnstile_enabled():
    return bool(os.environ.get("TURNSTILE_SITE_KEY") and os.environ.get("TURNSTILE_SECRET_KEY"))


def verify_turnstile(token, ip=None):
    """
    True if the visitor passed the Turnstile check -- or if Turnstile isn't
    configured. If Cloudflare itself is unreachable we let the visitor through
    (and log it) instead of locking every student out of login.
    """
    if not turnstile_enabled():
        return True
    if not token:
        return False
    try:
        payload = urllib.parse.urlencode({
            "secret": os.environ["TURNSTILE_SECRET_KEY"],
            "response": token,
            "remoteip": ip or "",
        }).encode()
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("success"))
    except Exception as exc:
        logger.warning("Turnstile verification unavailable (%s) -- letting request through", type(exc).__name__)
        return True


# ======================================================================
# 4. Security headers
# ======================================================================

def apply_security_headers(response):
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        h.setdefault("Strict-Transport-Security", "max-age=15552000")
    return response

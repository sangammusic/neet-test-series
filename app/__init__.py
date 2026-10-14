import logging
import os
from datetime import timedelta

from flask import Flask, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import cache

logger = logging.getLogger(__name__)


def _env_flag(name, default):
    """True/False from an environment variable such as SESSION_COOKIE_SECURE=1."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ["SECRET_KEY"]
    cache.init_app(app)

    from app.shared.converters import NameConverter
    app.url_map.converters["name"] = NameConverter

    # ------------------------------------------------------------------
    # PHASE 0 -- SECURITY BASICS
    # ------------------------------------------------------------------

    # 1. Behind Render's load balancer every request "comes from" the proxy.
    #    ProxyFix makes request.remote_addr the REAL visitor IP and request.is_secure
    #    correct (needed for rate limits, Secure cookies and https URLs).
    #    If you later put Cloudflare IN FRONT of Render, set TRUSTED_PROXY_COUNT=2.
    try:
        proxies = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))
    except ValueError:
        proxies = 1
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=1)

    # 2. Session cookie flags.
    #    HttpOnly  -> JavaScript can't read it        (default, stated for clarity)
    #    SameSite  -> other websites can't make the browser send it on a form POST
    #    Secure    -> only ever sent over https (switched off for local development)
    is_dev = (os.environ.get("FLASK_ENV") or "").strip().lower() == "development"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", default=not is_dev),
    )

    # BUGFIX: without PERMANENT_SESSION_LIFETIME + session.permanent = True
    # (set per-login in app/auth/routes.py), Flask treats the login cookie
    # as a browser-session cookie -- it can be dropped on a tab close, a
    # redirect chain, or a server restart, which is what caused "login
    # immediately asks to log in again". 7 days is a reasonable default;
    # adjust if you want shorter/longer persistent logins.
    app.permanent_session_lifetime = timedelta(days=7)

    # 3. CSRF protection: every POST must come from our own site (Origin/Referer check).
    #    The Telegram webhook is called by Telegram's servers, not by browsers, and
    #    authenticates itself with a secret header, so it is exempt.
    from app.shared.security import (
        init_origin_guard, apply_security_headers, error_response,
        turnstile_enabled, rl_hit, client_ip,
    )
    init_origin_guard(app, exempt_endpoints=("payments.tg_webhook",))

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from app.auth import auth_bp
    from app.user import user_bp
    from app.admin import admin_bp
    from app.payments import payments_bp
    from app.legal import legal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(legal_bp)

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------
    from app.shared.access import (
        format_inr, format_ist_date, format_ist_datetime, premium_status,
    )
    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["ist_date"] = format_ist_date
    app.jinja_env.filters["ist_datetime"] = format_ist_datetime

    @app.context_processor
    def _template_globals():
        return {
            # call it as premium_status() -- it only hits the database if a template asks
            "premium_status": premium_status,
            "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", "") if turnstile_enabled() else "",
            "support_url": os.environ.get("SUPPORT_URL", "https://t.me/s_u_k_oo_n_143"),
        }

    # ------------------------------------------------------------------
    # Friendly error pages. They never show exception text or stack traces.
    # ------------------------------------------------------------------
    @app.errorhandler(HTTPException)
    def _http_error(exc):
        code = exc.code or 500
        if 300 <= code < 400:          # redirects can be raised as exceptions: pass through
            return exc
        return error_response(code)

    # Lightweight health-check for uptime monitors (UptimeRobot etc).
    # Deliberately touches NO database and does NO auth/session work --
    # point external pingers here instead of at a real page like
    # /dashboard, so their periodic pings don't burn CPU cycles or
    # count against Supabase's request volume on the free tier.
    @app.route("/ping")
    def ping():
        return "OK", 200

    # Same idea, but it DOES touch the database with one tiny read.
    # Supabase's free plan pauses a project after 7 days without database
    # activity; point ONE monitor (every 5-10 minutes) at /ping-db to keep it awake.
    @app.route("/ping-db")
    def ping_db():
        if rl_hit("ping-db", client_ip(), 60, 60):
            return "Slow down", 429
        try:
            from app.extensions import supabase_admin
            supabase_admin.table("roles").select("id").limit(1).execute()
            return "OK", 200
        except Exception:
            logger.exception("ping-db: database check failed")
            return "Database unavailable", 503

    # BUGFIX: mobile back-button "loop" into finished quizzes/tests.
    #
    # What was happening: after Finish/Submit, JS navigates to the
    # result page (test_attempt.html -> window.location.href). That's
    # a normal navigation, so it pushes a new history entry -- history
    # now looks like [..., attempt_page, result_page]. When the user
    # then taps the hardware/gesture back button, mobile browsers don't
    # re-request attempt_page from the server -- they restore it from
    # the in-memory "back-forward cache" (bfcache): the exact DOM/JS
    # state the page was in right before navigating away, answers and
    # all. The server-side guards in app/user/routes.py (redirect to
    # test_result if attempt.submitted_at is already set, etc.) are
    # correct, but they never run on a bfcache restore, because no
    # request reaches the server at all. Same underlying mechanism was
    # affecting the practice runner and any other page reached after a
    # completed/consumed action.
    #
    # Fix: tell browsers not to bfcache OR disk-cache any dynamic
    # (non-static) response. `no-store` is the one directive that all
    # major mobile browsers (Chrome/Android, Safari/iOS) treat as "do
    # not put this page in bfcache". Back then forces a real reload,
    # which re-runs the server-side redirect logic and lands the user
    # on the correct, current page instead of a stale snapshot.
    #
    # Scoped to non-static responses only -- CSS/JS/images under
    # /static still cache normally, so this doesn't hurt load times.
    @app.after_request
    def _headers_for_every_response(response):
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return apply_security_headers(response)

    return app

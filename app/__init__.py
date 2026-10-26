# STUDY SANGAM HUB v5.0 — Production-Ready NEET UG Learning Platform
import logging
import os
import threading
from datetime import timedelta

from flask import Flask, Response, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import cache

logger = logging.getLogger(__name__)
_WEBHOOK_BOOTSTRAPPED = False

_FALLBACK_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
    '<defs>'
    '<linearGradient id="sgBg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#09111E"/>'
    '<stop offset="100%" stop-color="#0D1B2E"/>'
    '</linearGradient>'
    '<linearGradient id="sgTeal" x1="0" y1="1" x2="1" y2="0">'
    '<stop offset="0%" stop-color="#0284C7"/>'
    '<stop offset="50%" stop-color="#06B6D4"/>'
    '<stop offset="100%" stop-color="#67E8F9"/>'
    '</linearGradient>'
    '<linearGradient id="sgGold" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#FDE047"/>'
    '<stop offset="100%" stop-color="#D4A017"/>'
    '</linearGradient>'
    '</defs>'
    '<rect width="114" height="114" x="3" y="3" rx="24" fill="url(#sgBg)" stroke="url(#sgGold)" stroke-width="3.5"/>'
    '<path d="M60 15L74 36L60 56L46 36Z" fill="url(#sgGold)" opacity="0.22"/>'
    '<polygon points="60,21 63.5,31 74,34.5 63.5,38 60,48 56.5,38 46,34.5 56.5,31" fill="url(#sgGold)"/>'
    '<path d="M24 52C38 48 50 53 58 63V92C48 84 36 81 24 85Z" fill="#1E3A8A" stroke="#60A5FA" stroke-width="2"/>'
    '<path d="M96 52C82 48 70 53 62 63V92C72 84 84 81 96 85Z" fill="#0F766E" stroke="#2DD4BF" stroke-width="2"/>'
    '<path d="M28 88C42 74 56 54 68 25C74 48 64 74 44 94C38 92 32 90 28 88Z" fill="url(#sgTeal)"/>'
    '<polygon points="60,72 67,85 60,98 53,85" fill="#38BDF8" stroke="#BAE6FD" stroke-width="1.5"/>'
    '</svg>'
)


def _env_flag(name, default):
    """True/False from an environment variable such as SESSION_COOKIE_SECURE=1."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _find_static_img_dir(static_folder):
    for sub in ("img", "Img"):
        candidate = os.path.join(static_folder, sub)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(static_folder, "img")


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ["SECRET_KEY"]
    cache.init_app(app)

    from app.shared.converters import NameConverter
    app.url_map.converters["name"] = NameConverter

    # 1. ProxyFix for Render / Cloudflare
    try:
        proxies = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))
    except ValueError:
        proxies = 1
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=1)

    # 2. Session cookie flags
    is_dev = (os.environ.get("FLASK_ENV") or "").strip().lower() == "development"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", default=not is_dev),
        SEND_FILE_MAX_AGE_DEFAULT=86400,
    )

    app.permanent_session_lifetime = timedelta(days=7)

    # 3. CSRF protection
    from app.shared.security import (
        init_origin_guard, apply_security_headers, error_response,
        turnstile_enabled, rl_hit, client_ip,
    )
    init_origin_guard(app, exempt_endpoints=("payments.tg_webhook",))

    # 4. Blueprints
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

    # 5. Template helpers
    from app.shared.access import (
        format_inr, format_ist_date, format_ist_datetime, premium_status,
    )
    from app.shared.latex_lint import clean_question_text
    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["ist_date"] = format_ist_date
    app.jinja_env.filters["ist_datetime"] = format_ist_datetime
    app.jinja_env.filters["clean_q"] = clean_question_text

    @app.context_processor
    def _template_globals():
        return {
            "premium_status": premium_status,
            "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", "") if turnstile_enabled() else "",
            "support_url": os.environ.get("SUPPORT_URL", "https://t.me/s_u_k_oo_n_143"),
        }

    # Zero-404 Logo & Favicon Guard (Case-insensitive for Linux / Render)
    @app.route("/favicon.ico")
    def favicon():
        img_dir = _find_static_img_dir(app.static_folder)
        for fname in ("favicon.png", "logo-mark.png", "logo.png"):
            if os.path.isfile(os.path.join(img_dir, fname)):
                return send_from_directory(img_dir, fname, mimetype="image/png")
        return Response(_FALLBACK_LOGO_SVG, mimetype="image/svg+xml")

    @app.route("/static/img/<path:filename>")
    @app.route("/static/Img/<path:filename>")
    def serve_static_img(filename):
        img_dir = _find_static_img_dir(app.static_folder)
        target = os.path.join(img_dir, filename)
        if os.path.isfile(target):
            return send_from_directory(img_dir, filename)
        return Response(_FALLBACK_LOGO_SVG, mimetype="image/svg+xml")

    # Auto-register Telegram Bot webhook on Render
    @app.before_request
    def _auto_bootstrap_tg_webhook():
        global _WEBHOOK_BOOTSTRAPPED
        if _WEBHOOK_BOOTSTRAPPED:
            return
        render_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
        tg_tok = (os.environ.get("TG_TOKEN") or "").strip()
        if render_url and tg_tok and render_url.startswith("https://"):
            _WEBHOOK_BOOTSTRAPPED = True
            def _bg():
                try:
                    from app.shared import payments_core as pay
                    pay.tg_set_webhook(render_url)
                except Exception:
                    pass
            threading.Thread(target=_bg, daemon=True).start()
        else:
            _WEBHOOK_BOOTSTRAPPED = True

    # Friendly error pages
    @app.errorhandler(HTTPException)
    def _http_error(exc):
        code = exc.code or 500
        if 300 <= code < 400:
            return exc
        return error_response(code)

    @app.errorhandler(Exception)
    def _unhandled_exception(exc):
        if isinstance(exc, HTTPException):
            return _http_error(exc)
        logger.exception("Unhandled exception on %s %s: %s", request.method, request.path, exc)
        return error_response(500)

    @app.route("/ping")
    def ping():
        return "OK", 200

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

    @app.after_request
    def _headers_for_every_response(response):
        if request.path.startswith("/static/") or request.path == "/favicon.ico":
            response.headers["Cache-Control"] = "public, max-age=86400"
        else:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return apply_security_headers(response)

    return app

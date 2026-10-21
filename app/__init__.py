# STUDY SANGAM HUB v3.0 — Handcrafted Responsive & Dual-Theme Build (2026-10-22)
import logging
import os
import threading
from datetime import timedelta

from flask import Flask, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import cache

logger = logging.getLogger(__name__)
_WEBHOOK_BOOTSTRAPPED = False


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
    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["ist_date"] = format_ist_date
    app.jinja_env.filters["ist_datetime"] = format_ist_datetime

    @app.context_processor
    def _template_globals():
        return {
            "premium_status": premium_status,
            "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", "") if turnstile_enabled() else "",
            "support_url": os.environ.get("SUPPORT_URL", "https://t.me/s_u_k_oo_n_143"),
        }

    # Auto-register Telegram Bot webhook on Render so /setqr and Approve/Disapprove
    # work automatically without needing any web admin payment panel
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
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return apply_security_headers(response)

    return app

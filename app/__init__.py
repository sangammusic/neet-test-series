import os
from datetime import timedelta
from flask import Flask

from app.extensions import cache


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ["SECRET_KEY"]
    cache.init_app(app)

    # BUGFIX: without PERMANENT_SESSION_LIFETIME + session.permanent = True
    # (set per-login in app/auth/routes.py), Flask treats the login cookie
    # as a browser-session cookie — it can be dropped on a tab close, a
    # redirect chain, or a server restart, which is what caused "login
    # immediately asks to log in again". 7 days is a reasonable default;
    # adjust if you want shorter/longer persistent logins.
    app.permanent_session_lifetime = timedelta(days=7)

    from app.auth import auth_bp
    from app.user import user_bp
    from app.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)

    # Lightweight health-check for uptime monitors (UptimeRobot etc).
    # Deliberately touches NO database and does NO auth/session work --
    # point external pingers here instead of at a real page like
    # /dashboard, so their periodic pings don't burn CPU cycles or
    # count against Supabase's request volume on the free tier.
    @app.route("/ping")
    def ping():
        return "OK", 200

    return app

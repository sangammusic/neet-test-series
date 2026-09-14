import os
from datetime import timedelta
from flask import Flask


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ["SECRET_KEY"]

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

    return app

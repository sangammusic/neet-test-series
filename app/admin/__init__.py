import os
from datetime import timedelta
from flask import Flask, request

from app.extensions import cache


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ["SECRET_KEY"]
    cache.init_app(app)

    from app.shared.converters import NameConverter
    app.url_map.converters["name"] = NameConverter

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
    def _disable_bfcache_for_dynamic_pages(response):
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    return app

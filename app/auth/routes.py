# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
import logging
import re

from flask import render_template, request, redirect, url_for, session, flash

from app.auth import auth_bp
from app.extensions import supabase_admin, new_auth_client
from app.shared.utils import is_logged_in
from app.shared.security import (
    client_ip, minutes_text, rl_blocked, rl_clear, rl_hit, rl_record, verify_turnstile,
)

logger = logging.getLogger(__name__)

# ---- abuse limits (see app/shared/security.py) --------------------------------
# Only FAILED logins count, so a student typing the right password is never slowed down.
LOGIN_FAIL_WINDOW = 600            # 10 minutes
LOGIN_FAIL_LIMIT_IP = 40           # a whole school can share one IP, so this is generous
LOGIN_FAIL_LIMIT_USER = 8          # per (IP + username): stops guessing one account's password
REGISTER_LIMIT_IP = 40             # new accounts per hour per IP (a classroom can sign up together)
REGISTER_WINDOW = 3600

USERNAME_MIN, USERNAME_MAX = 3, 20


def is_valid_username(username):
    """
    Security check: Only allows alphanumeric characters and underscores.
    Blocks spaces, @, dots, and other special characters to prevent injection.
    """
    return bool(re.match(r"^[a-zA-Z0-9_]+$", username))


def _start_session(user_id):
    """
    Logs the user in. Clears anything left in the cookie first.
    NOTE: the Supabase access_token is deliberately NOT stored any more -- nothing
    in this app used it, and the session cookie is only signed, not encrypted.
    """
    session.clear()
    session.permanent = True
    session["user_id"] = user_id


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_logged_in():
            return redirect(url_for("user.start_learning"))
        return render_template("login.html")

    ip = client_ip()
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    user_key = f"{ip}|{username}"

    # 0. Too many recent failures from this address / for this account from this address?
    wait = max(
        rl_blocked("login-ip", ip, LOGIN_FAIL_LIMIT_IP, LOGIN_FAIL_WINDOW),
        rl_blocked("login-user", user_key, LOGIN_FAIL_LIMIT_USER, LOGIN_FAIL_WINDOW) if username else 0,
    )
    if wait:
        flash(f"Too many failed attempts. Please try again in {minutes_text(wait)}.", "error")
        return render_template("login.html"), 429

    # 1. Bot check (does nothing until Turnstile keys are configured)
    if not verify_turnstile(request.form.get("cf-turnstile-response"), ip):
        flash("Please complete the security check and try again.", "error")
        return render_template("login.html"), 400

    # 2. Backend validation
    if not username or not is_valid_username(username):
        rl_record("login-ip", ip, LOGIN_FAIL_WINDOW)
        flash("Invalid username format. Only letters, numbers, and underscores allowed.", "error")
        return render_template("login.html"), 400

    # 3. Convert to dummy email, then check the password with a THROW-AWAY client
    #    (never with the shared database client -- see app/extensions.py).
    dummy_email = f"{username}@sangam.local"
    try:
        result = new_auth_client().auth.sign_in_with_password(
            {"email": dummy_email, "password": password}
        )
    except Exception:
        rl_record("login-ip", ip, LOGIN_FAIL_WINDOW)
        rl_record("login-user", user_key, LOGIN_FAIL_WINDOW)
        flash("Invalid username or password.", "error")
        return render_template("login.html"), 401

    if not result.session or not result.user:
        flash("Something went wrong. Please try again.", "error")
        return render_template("login.html"), 401

    rl_clear("login-user", user_key)
    _start_session(result.user.id)
    return redirect(url_for("user.start_learning"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        if is_logged_in():
            return redirect(url_for("user.start_learning"))
        return render_template("register.html")

    ip = client_ip()
    wait = rl_hit("register-ip", ip, REGISTER_LIMIT_IP, REGISTER_WINDOW)
    if wait:
        flash(f"Too many sign-ups from this network. Please try again in {minutes_text(wait)}.", "error")
        return render_template("register.html"), 429

    if not verify_turnstile(request.form.get("cf-turnstile-response"), ip):
        flash("Please complete the security check and try again.", "error")
        return render_template("register.html"), 400

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    full_name = request.form.get("full_name", "").strip()[:80]

    # 1. Backend Validation (Injection Prevention)
    if not username or not is_valid_username(username):
        flash("Invalid username. Only letters, numbers, and underscores are allowed (no spaces or @).", "error")
        return render_template("register.html"), 400

    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        flash(f"Username must be {USERNAME_MIN} to {USERNAME_MAX} characters long.", "error")
        return render_template("register.html"), 400

    # 2. Password must be entered twice and must match.
    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return render_template("register.html"), 400

    if not confirm_password:
        flash("Please confirm your password.", "error")
        return render_template("register.html"), 400

    if password != confirm_password:
        flash("Password and confirmation do not match.", "error")
        return render_template("register.html"), 400

    # 3. Convert to Dummy Email
    dummy_email = f"{username}@sangam.local"

    try:
        result = supabase_admin.auth.admin.create_user(
            {
                "email": dummy_email,
                "password": password,
                "email_confirm": True,
            }
        )
    except Exception as exc:
        error_msg = str(exc).lower()
        # Duplicate dummy email = username already taken
        if ("already" in error_msg and "registered" in error_msg) or "unique" in error_msg:
            flash(f"Username '{username}' is already taken! Please choose another one.", "error")
        else:
            # SECURITY: never show the raw exception to the visitor. Log it for us instead.
            logger.error("Registration failed for a new account: %s", exc)
            flash("We couldn't create your account right now. Please try again in a moment.", "error")
        return render_template("register.html"), 400

    if result.user:
        try:
            # Save the unique username to the profiles table
            supabase_admin.table("profiles").insert(
                {
                    "id": result.user.id,
                    "full_name": full_name,
                    "username": username
                }
            ).execute()
        except Exception as db_exc:
            # Failsafe: if the profile insert fails, delete the auth user so they aren't stuck in limbo
            logger.error("Profile insert failed during registration: %s", db_exc)
            try:
                supabase_admin.auth.admin.delete_user(result.user.id)
            except Exception as del_exc:
                logger.error("Could not roll back auth user after failed profile insert: %s", del_exc)
            flash(f"Username '{username}' is already taken! Please choose another.", "error")
            return render_template("register.html"), 400
    else:
        flash("We couldn't create your account right now. Please try again in a moment.", "error")
        return render_template("register.html"), 400

    try:
        sign_in_result = new_auth_client().auth.sign_in_with_password(
            {"email": dummy_email, "password": password}
        )
    except Exception:
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    if not sign_in_result.session or not sign_in_result.user:
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    _start_session(sign_in_result.user.id)
    flash("Account created — you're all set.", "success")
    return redirect(url_for("user.start_learning"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# NOTE: guest_start() has been permanently removed. There is no more
# "Continue without login" path anywhere in this app -- every visitor
# hits the login page first. See app/user/routes.py: landing() now
# redirects straight to auth.login for anonymous visitors.
